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

RUN_DIR = Path.home() / ".modelweaver" / "run"
RECIPE_BASE = REPO_ROOT / "modules" / "installer"


def _mw_dir() -> Path:
    d = Path.home() / ".modelweaver"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _db_paths() -> tuple[Path, Path]:
    """Chemins DB stables sous ~/.modelweaver (indépendants du CWD)."""
    mw_dir = _mw_dir()
    return mw_dir / "modelweaver.db", mw_dir / "catalogue.db"


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


def acquire_instance_lock(name: str) -> bool:
    """Garantit un seul processus par `name` (single-instance).

    Crée ~/.modelweaver/run/<name>.pid. Si un PID vivant y est déjà présent,
    retourne False (l'appelant doit s'arrêter). Sinon écrit le PID courant,
    enregistre un cleanup à la sortie, et retourne True.

    Le superviseur utilise ce même mécanisme pour chacun de ses services, ainsi
    que pour lui-même (lock 'supervisor').
    """
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    lock = RUN_DIR / f"{name}.pid"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False

    try:
        cur = lock.read_text().strip() if lock.exists() else ""
    except OSError:
        cur = ""
    if cur:
        try:
            pid = int(cur)
            os.kill(pid, 0)  # lève OSError si le processus n'existe pas
            os.close(fd)
            return False  # déjà en cours
        except (ValueError, OSError, ProcessLookupError):
            pass  # pid mort -> on réutilise le lock

    lock.write_text(str(os.getpid()))
    atexit.register(_release_pid_file, lock)
    return True


def _release_pid_file(lock: Path) -> None:
    try:
        if lock.read_text().strip() == str(os.getpid()):
            lock.unlink(missing_ok=True)
    except Exception:
        pass
