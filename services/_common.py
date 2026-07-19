#!/usr/bin/env python3
"""Helpers partagés des services ModelWeaver.

Fournit l'ancrage du dépôt sur sys.path, la résolution des chemins DB, la
redirection propre de stdout, la journalisation fichier, et le verrou
d'instance unique (single-instance) utilisé par le superviseur pour garantir
qu'un seul processus par service (et le superviseur lui-même) tourne à la fois.
"""
import sys
import os
import fcntl
import json
import time
import signal
import atexit
import contextlib
import io
from pathlib import Path
from typing import Optional

# ── Ancrage du dépôt sur sys.path (modules/, services/, sql/ à la racine) ──
_SERVICE_DIR = Path(__file__).resolve().parent          # services/
REPO_ROOT = _SERVICE_DIR.parent.parent                   # racine du repo
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def mw_home() -> Path:
    """Racine d'installation ModelWeaver (données + code app).

    Résolution (partagée avec le binaire Rust) :
      1. MODELWEAVER_HOME si défini
      2. /opt/modelweaver si présent (install system-wide)
      3. sinon ~/.modelweaver (dev / user local, sans sudo)
    """
    ev = os.environ.get("MODELWEAVER_HOME")
    if ev:
        return Path(ev)
    opt = Path("/opt/modelweaver")
    if opt.exists():
        return opt
    return Path.home() / ".modelweaver"


RUN_DIR = mw_home() / "run"
RECIPE_BASE = REPO_ROOT / "modules" / "installer"


def _mw_dir() -> Path:
    d = mw_home()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_paths() -> tuple[Path, Path]:
    """Chemins DB stables sous mw_home() (indépendants du CWD).

    (modelweaver.db = inventaire local, catalogue.db = catalogue distant)
    """
    mw_dir = _mw_dir()
    return mw_dir / "modelweaver.db", mw_dir / "catalogue.db"


def runtime_db_path() -> Path:
    """DBRuntime : écritures haute fréquence (processus, services, jobs d'install).

    Isolée de l'inventaire/catalogue pour éviter la contention (le GUI y écrit
    directement, le daemon et l'installer_worker aussi).
    """
    return _mw_dir() / "runtime.db"


@contextlib.contextmanager
def _quiet_stdout():
    """Redirige stdout vers stderr le temps d'un appel qui print (sync/install)."""
    old = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = old


def log_to_file(level: str, message: str) -> None:
    try:
        from datetime import datetime
        log_dir = _mw_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "installer.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} [{level}] {message}\n")
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _pid_matches_service(pid: int, name: str) -> bool:
    """Vrai si le processus `pid` correspond bien au service `name`.

    Lit /proc/<pid>/cmdline et vérifie la présence d'un marqueur lié au
    service (ex. 'daemon.py' pour l'api, 'catalogue', 'installer_worker', ...).
    Évite de tuer un processus innocent en cas de réutilisation de PID.
    """
    try:
        cmdline = (RUN_DIR.parent / f"../../proc/{pid}/cmdline").read_bytes()
    except OSError:
        # Pas de /proc (non-unix ou process parti) : on ne peut pas confirmer,
        # on refuse de tuer par sécurité.
        return False
    text = cmdline.replace(b"\x00", b" ").decode("utf-8", "replace")
    # Marqueurs attendus par service.
    markers = {
        "api": ("daemon.py",),
        "catalogue": ("catalogue",),
        "installer": ("installer_worker",),
        "tester": ("tester",),
        "supervisor": ("modelweaver",),
    }.get(name, (name,))
    return any(m in text for m in markers)


def acquire_instance_lock(name: str) -> bool:
    """Garantit un seul processus par `name` (single-instance).

    Crée mw_home/run/<name>.pid. Si un PID vivant y est déjà présent, le
    processus est tué (SIGTERM puis SIGKILL) et le verrou réutilisé : le
    nouveau prend la place de l'ancien sans toucher à la base (qui est sur
    disque). Ainsi, relancer un superviseur/daemon le remplace proprement
    au lieu d'échouer sur un verrou périmé.
    """
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    lock = RUN_DIR / f"{name}.pid"

    # Kill-and-replace : si un ancien détenteur vivant détient le verrou, on le tue.
    # Sécurité anti-PID-reuse : on ne tue que si le processus correspond bien au
    # service attendu (vérifié via /proc/<pid>/cmdline), sinon on nettoie juste
    # le fichier de verrou périmé sans toucher au processus étranger.
    if lock.exists():
        try:
            pid = int(lock.read_text().strip())
            if _pid_alive(pid) and _pid_matches_service(pid, name):
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
                for _ in range(50):
                    if not _pid_alive(pid):
                        break
                    time.sleep(0.1)
                else:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
        except (ValueError, OSError, ProcessLookupError):
            pass
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        fd = os.open(lock, os.O_CREAT | os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False

    lock.write_text(str(os.getpid()))
    atexit.register(_release_pid_file, lock)
    return True


def _release_pid_file(lock: Path) -> None:
    try:
        if lock.read_text().strip() == str(os.getpid()):
            lock.unlink(missing_ok=True)
    except Exception:
        pass
