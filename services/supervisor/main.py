#!/usr/bin/env python3
"""Superviseur — process racine qui lance et supervise tous les services.

Principes :
  1. Un seul superviseur (singleton via acquire_instance_lock("supervisor")).
  2. Au boot : scanne /proc, adopte les services déjà en cours (pas de
     doublon), lance les absents avec leur socket par défaut.
  3. Si un socket par défaut est pris par un process externe → attribue le
     prochain libre et l'enregistre.
  4. Boucle de supervision : relance les morts (sauf arrêt manuel).
  5. Expose un socket Unix `supervisor.sock` (status/start/stop/restart/list/
     shutdown) — le redémarrage ET l'arrêt passent par lui.
  6. Registre des services dans ~/.modelweaver/run/sockets.json (primaire)
     + miroir en BDD (runtime.db) pour la redondance.

Le daemon est géré par le superviseur (pas l'inverse) ; le daemon, de son
côté, surveille le superviseur et le relance s'il meurt.
"""

import fcntl
import json
import os
import signal
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from services._common import RUN_DIR, mw_home, acquire_instance_lock  # noqa: E402

MANIFESTS_DIR = REPO_ROOT / "services" / "manifests"
REGISTRY_FILE = RUN_DIR / "sockets.json"
SUPERVISOR_SOCK = mw_home() / "supervisor.sock"

# Ports TCP par défaut par service (fallback si manifest sans port).
DEFAULT_TCP_PORTS = {
    "api": 8770,
    "catalogue": 8765,
}
# Sockets Unix par défaut par service.
DEFAULT_UNIX_SOCKS = {
    "supervisor": SUPERVISOR_SOCK,
    "afd": mw_home() / "afd.sock",
    "llm": mw_home() / "llm.sock",
    "ressource_manager": mw_home() / "ressources.sock",
}

SUPERVISE_INTERVAL = 5.0


# ── Registre (source primaire /run + miroir BDD) ─────────────────────────

class Registry:
    """Registre des services : {name: {pid, socket, status, restarts}}."""

    def __init__(self):
        self.data: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if REGISTRY_FILE.exists():
            try:
                self.data = json.loads(REGISTRY_FILE.read_text())
            except Exception:
                self.data = {}
        # Compléter depuis la BDD si /run est vide (redondance)
        if not self.data:
            try:
                rows = self._db_rows()
                for name, pid, socket_, status in rows:
                    self.data[name] = {"pid": pid, "socket": socket_,
                                       "status": status, "restarts": 0}
            except Exception:
                pass

    def save(self):
        try:
            REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
            REGISTRY_FILE.write_text(json.dumps(self.data, indent=1))
        except Exception:
            pass
        try:
            self._db_save()
        except Exception:
            pass

    def _db_rows(self) -> List[tuple]:
        import sqlite3
        db = mw_home() / "runtime.db"
        conn = sqlite3.connect(str(db), timeout=2)
        try:
            cur = conn.execute(
                "SELECT name, pid, socket_addr, status FROM services")
            return cur.fetchall()
        finally:
            conn.close()

    def _db_save(self):
        import sqlite3
        db = mw_home() / "runtime.db"
        conn = sqlite3.connect(str(db), timeout=2, isolation_level=None)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS services (
                name TEXT PRIMARY KEY, pid INTEGER, socket_addr TEXT,
                status TEXT, updated_at INTEGER)""")
            now = int(time.time())
            for name, info in self.data.items():
                conn.execute(
                    "INSERT INTO services (name, pid, socket_addr, status, updated_at)"
                    " VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET"
                    " pid=excluded.pid, socket_addr=excluded.socket_addr,"
                    " status=excluded.status, updated_at=excluded.updated_at",
                    (name, info.get("pid"), info.get("socket"),
                     info.get("status", "unknown"), now))
        finally:
            conn.close()

    def get(self, name: str) -> Optional[dict]:
        return self.data.get(name)

    def set(self, name: str, info: dict):
        self.data[name] = info

    def all(self) -> Dict[str, dict]:
        return dict(self.data)


# ── Détection de process / sockets ──────────────────────────────────────

def _list_processes() -> List[tuple]:
    """Retourne [(pid, cmdline)] des process applicatifs (exclut shells)."""
    out = []
    self_pid = os.getpid()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if pid == self_pid:
            continue
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
        if not cmdline:
            continue
        # Exclure les shells / interpréteurs de commande (faux positifs :
        # le terminal qui lance le superviseur contient "daemon.py" etc.)
        first = cmdline.split()[0] if cmdline.split() else ""
        if any(b in first for b in ("bash", "sh", "zsh", "fish", "dash", "kash")):
            continue
        out.append((pid, cmdline))
    return out


def _match_service(cmdline: str, name: str) -> bool:
    """Vrai si la cmdline correspond au service `name`.

    Les manifests utilisent des noms en tirets (llm-manager, model-sync,
    agent-manager) alors que les modules/cmdlines réelles sont en
    underscores (llm_manager/service.py) : on normalise les deux côtés
    pour que l'adoption des services déjà en cours fonctionne (sinon le
    superviseur relance un doublon à chaque boot).
    """
    markers = {
        "api": ("daemon.py serve", "daemon.py",),
        "catalogue": ("catalogue/service.py",),
        "model_sync": ("model_sync.py",),
        "usage_collector": ("usage_collector.py",),
        "supervisor": ("supervisor/main.py",),
        "llm_manager": ("llm_manager/service.py",),
        "ressource_manager": ("ressource_manager/service.py",),
        "afd": ("afd/service.py",),
        "tester": ("tester",),
        "installer_worker": ("installer_worker",),
        "agent_manager": ("agent_manager.service",),
        "watcher": ("watcher/watcher.py",),
    }
    norm = name.lower().replace("-", "_")
    marks = markers.get(norm, (norm,))
    return any(m in cmdline for m in marks)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # Un zombie (<defunct>) existe encore dans /proc mais est mort : le
    # superviseur ne doit pas le considérer vivant (sinon il ne le relance
    # jamais et les doublons s'accumulent).
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        state = stat.split(")")[-1].split()[0].strip() if ")" in stat else ""
        if state == "Z":
            return False
    except OSError:
        pass
    return True


def _find_existing(procs: List[tuple], name: str) -> Optional[int]:
    """Cherche dans /proc un process correspondant au service (adoption)."""
    for pid, cmdline in procs:
        if _match_service(cmdline, name) and cmdline != "" \
                and _same_home(pid):
            return pid
    return None


def _socket_from_cmdline(procs: List[tuple], pid: int, name: str) -> Optional[str]:
    """Extrait le socket réel d'un process adopté depuis sa cmdline.

    Port TCP : lit ``--port <N>`` (ex. daemon, catalogue). Socket Unix :
    lit ``--sock <path>`` si présent. Retourne None si non déterminable.
    """
    for p, cmdline in procs:
        if p != pid:
            continue
        import re
        m = re.search(r"--port\s+(\d+)", cmdline)
        if m:
            return m.group(1)
        m = re.search(r"--sock\s+(\S+)", cmdline)
        if m:
            return m.group(1)
        return ""
    return None


# ── Attribution de sockets ──────────────────────────────────────────────

def _tcp_port_in_use(port: int) -> bool:
    """Vrai si le port TCP est déjà écouté (bind de test)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _find_free_tcp(preferred: int) -> int:
    port = preferred
    for _ in range(100):
        if not _tcp_port_in_use(port):
            return port
        port += 1
    raise RuntimeError(f"aucun port libre autour de {preferred}")


def _unix_sock_in_use(path: Path) -> bool:
    """Vrai si le socket Unix est vivant (fichier + connectable)."""
    if not path.exists():
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(str(path))
            return True
        finally:
            s.close()
    except OSError:
        return False


def _resolve_socket(name: str, spec: dict, registry: Registry) -> str:
    """Résout l'adresse du socket pour un service (port TCP ou path Unix).

    Si le socket par défaut est pris par un service qu'on gère déjà (adopté),
    on le garde. Si pris par un inconnu → on prend le prochain libre.
    """
    # Socket Unix
    unix_default = spec.get("unix_socket")
    if unix_default:
        path = Path(unix_default)
        existing = registry.get(name)
        if existing and existing.get("socket") == str(path) and _pid_alive(existing.get("pid", -1)):
            return str(path)
        if _unix_sock_in_use(path):
            # Occupé par un process externe → choisir un autre path
            alt = mw_home() / f"{name}.sock"
            n = 2
            while _unix_sock_in_use(alt):
                alt = mw_home() / f"{name}-{n}.sock"
                n += 1
            return str(alt)
        # Fichier mort → le nettoyer pour libérer le path
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return str(path)

    # Port TCP
    preferred = spec.get("port") or DEFAULT_TCP_PORTS.get(name)
    if preferred is None:
        return ""
    existing = registry.get(name)
    if existing and existing.get("socket") and _pid_alive(existing.get("pid", -1)):
        return str(existing["socket"])
    port = _find_free_tcp(preferred) if _tcp_port_in_use(preferred) else preferred
    return str(port)


def _expand_args(args: List[str], mw_home_path: Path) -> List[str]:
    """Remplit {{mw_home}} et remplace --port par l'adresse résolue."""
    out = []
    for a in args:
        a = a.replace("{{mw_home}}", str(mw_home_path))
        out.append(a)
    return out


# ── Lancement / adoption des services ───────────────────────────────────

def _load_manifests() -> Dict[str, dict]:
    """Charge les manifests .service.yaml → {name: spec dict}.

    Un même name peut exister dans 2 manifests (ex. chat-installer mode
    agent + installer-worker avec command) : on privilégie celui qui a une
    commande réelle (process supervisé), les agents-as-service étant gérés
    par le daemon.
    """
    import yaml
    specs = {}
    if not MANIFESTS_DIR.exists():
        return specs
    for path in sorted(MANIFESTS_DIR.glob("*.service.yaml")):
        try:
            raw = yaml.safe_load(path.read_text())
            name = raw.get("name")
            if not name:
                continue
            launch = raw.get("launch", {}) or {}
            has_cmd = bool(launch.get("command"))
            prev = specs.get(name)
            prev_has_cmd = bool(prev and (prev.get("launch") or {}).get("command"))
            if prev is not None and prev_has_cmd and not has_cmd:
                continue  # garder la version avec commande réelle
            specs[name] = raw
        except Exception as e:
            print(f"[supervisor] manifest invalide {path}: {e}", flush=True)
    return specs


class Supervisor:
    def __init__(self):
        self.registry = Registry()
        self.manifests = _load_manifests()
        self.procs: Dict[str, subprocess.Popen] = {}
        self.manual_stop: set = set()  # services arrêtés manuellement (pas de relance)
        self._running = True
        self._sock: Optional[socket.socket] = None

    # ── Boot : adopter ou lancer ──

    def boot(self):
        # Relance volontaire : on réarme la supervision (annule un éventuel
        # shutdown_all précédent sans redémarrage explicite).
        try:
            (mw_home() / "supervisor.disabled").unlink(missing_ok=True)
        except OSError:
            pass
        procs = _list_processes()
        print(f"[supervisor] boot : {len(self.manifests)} manifests, "
              f"{len(procs)} process", flush=True)
        for name, spec in self.manifests.items():
            # Ignorer les agents-as-service (in-process du daemon, pas de cmd)
            launch = spec.get("launch", {}) or {}
            if not launch.get("command") or launch.get("mode") == "agent":
                continue
            existing_pid = _find_existing(procs, name)
            if existing_pid is not None and _pid_alive(existing_pid):
                # Service déjà en cours : on adopte son socket RÉEL (celui
                # qu'il utilise), pas un nouveau — éviter de déclarer 8771
                # pour un daemon qui tourne déjà sur 8770.
                sock_addr = _socket_from_cmdline(procs, existing_pid, name) or ""
                self.registry.set(name, {"pid": existing_pid,
                                         "socket": sock_addr,
                                         "status": "running", "restarts": 0})
                print(f"[supervisor] adopté {name} (pid {existing_pid}, "
                      f"socket {sock_addr})", flush=True)
            else:
                sock_addr = _resolve_socket(name, spec, self.registry)
                self._launch(name, spec, sock_addr)
        self.registry.save()

    def _launch(self, name: str, spec: dict, sock_addr: str) -> bool:
        launch = spec.get("launch", {}) or {}
        cmd = launch.get("command", "")
        args = launch.get("args", [])
        if not cmd:
            return False
        # Garde anti-doublon : si un process du service est déjà vivant
        # (ex. le daemon a déjà lancé model-sync), on l'adopte au lieu de
        # créer un second.
        existing = self.registry.get(name)
        if existing and _pid_alive(existing.get("pid", -1)):
            existing["status"] = "running"
            self.registry.set(name, existing)
            return True
        cmd_parts = shlex.split(cmd)
        # Remplacer --port par le port résolu (TCP) si présent
        if sock_addr and sock_addr.isdigit() and "--port" in args:
            idx = args.index("--port")
            args[idx + 1] = sock_addr
        args = _expand_args(args, mw_home())
        workdir = launch.get("workdir") or str(REPO_ROOT)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        try:
            proc = subprocess.Popen(
                cmd_parts + args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=workdir,
                env=env,
            )
            self.procs[name] = proc
            self.registry.set(name, {"pid": proc.pid, "socket": sock_addr,
                                     "status": "running", "restarts": 0})
            print(f"[supervisor] lancé {name} (pid {proc.pid}, "
                  f"socket {sock_addr})", flush=True)
            return True
        except Exception as e:
            print(f"[supervisor] échec lancement {name}: {e}", flush=True)
            self.registry.set(name, {"pid": -1, "socket": sock_addr,
                                     "status": "failed", "restarts": 0})
            return False

    # ── Supervision ──

    def supervise_once(self):
        # Récolter les process morts qu'on a lancés (évite les zombies).
        # On note le code de sortie des services `once` pour le traitement
        # ci-dessous (code 0 = travail terminé, pas de relance).
        exited_codes = {}
        for name, proc in list(self.procs.items()):
            if proc.poll() is not None:
                code = None
                try:
                    code = proc.poll()
                    proc.wait()
                except Exception:
                    pass
                try:
                    self.procs.pop(name, None)
                except Exception:
                    pass
                exited_codes[name] = code
        for name, info in list(self.registry.all().items()):
            pid = info.get("pid", -1)
            status = info.get("status", "unknown")
            if status in ("stopped_manual", "stopped", "done"):
                continue
            if _pid_alive(pid):
                continue
            if name in self.manual_stop:
                continue
            spec = self.manifests.get(name)
            launch = (spec or {}).get("launch", {}) or {}
            if not launch.get("command") or launch.get("mode") == "agent":
                continue
            mode = launch.get("mode", "loop")
            # Service `once` : une sortie en code 0 = travail terminé → on ne
            # relance PAS (status "done"). Seul un code non-zéro (échec réel)
            # déclenche la relance.
            if mode == "once":
                code = exited_codes.get(name)
                if code == 0:
                    self.registry.set(name, {**info, "status": "done"})
                    self.registry.save()
                    print(f"[supervisor] {name} terminé (code 0) — done", flush=True)
                    continue
                # code non-zéro ou inconnu → échec, relance normale ci-dessous
            restart = bool((spec or {}).get("supervisor", {}).get("restart", True))
            if not restart:
                self.registry.set(name, {**info, "status": "crashed"})
                continue
            max_restarts = int((spec or {}).get("supervisor", {}).get("max_restarts", 3))
            restarts = int(info.get("restarts", 0))
            if max_restarts > 0 and restarts >= max_restarts:
                self.registry.set(name, {**info, "status": "exhausted"})
                print(f"[supervisor] {name} épuisé ({restarts} restarts)", flush=True)
                continue
            # Relancer
            new_sock = _resolve_socket(name, spec, self.registry)
            ok = self._launch(name, spec, new_sock)
            if ok:
                self.registry.set(name, {
                    **self.registry.get(name),
                    "restarts": restarts + 1,
                    "status": "running",
                })
                print(f"[supervisor] relancé {name} (pid "
                      f"{self.registry.get(name).get('pid')})", flush=True)
            self.registry.save()

    def supervise_loop(self):
        while self._running:
            try:
                self.supervise_once()
            except Exception as e:
                print(f"[supervisor] erreur supervision: {e}", flush=True)
            time.sleep(SUPERVISE_INTERVAL)

    # ── Commandes (socket) ──

    def status(self) -> dict:
        return {"status": "ok", "supervisor_pid": os.getpid(),
                "services": self.registry.all()}

    def list_services(self) -> dict:
        return {"status": "ok", "services": self.registry.all()}

    def start(self, name: str) -> dict:
        spec = self.manifests.get(name)
        if not spec:
            return {"status": "error", "error": f"service inconnu: {name}"}
        self.manual_stop.discard(name)
        sock = _resolve_socket(name, spec, self.registry)
        if self._launch(name, spec, sock):
            self.registry.save()
            return {"status": "ok", "pid": self.registry.get(name).get("pid"),
                    "socket": sock}
        return {"status": "error", "error": f"échec lancement {name}"}

    def stop(self, name: str) -> dict:
        info = self.registry.get(name)
        pid = info.get("pid", -1) if info else -1
        self.manual_stop.add(name)
        if info:
            info["status"] = "stopped_manual"
            self.registry.set(name, info)
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(25):
                    if not _pid_alive(pid):
                        break
                    time.sleep(0.1)
                else:
                    os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        self.registry.save()
        return {"status": "ok", "stopped": name}

    def restart(self, name: str) -> dict:
        self.stop(name)
        self.manual_stop.discard(name)
        spec = self.manifests.get(name)
        if not spec:
            return {"status": "error", "error": f"service inconnu: {name}"}
        sock = _resolve_socket(name, spec, self.registry)
        if self._launch(name, spec, sock):
            self.registry.save()
            return {"status": "ok", "pid": self.registry.get(name).get("pid"),
                    "socket": sock}
        return {"status": "error", "error": f"échec relance {name}"}

    def shutdown(self) -> dict:
        """Arrêt complet : stoppe tous les services puis le superviseur."""
        for name in list(self.registry.all().keys()):
            try:
                self.stop(name)
            except Exception:
                pass
        self._running = False
        return {"status": "ok", "message": "arrêt complet demandé"}

    def reset(self, name: str) -> dict:
        """Reset complet : arrêt, remise à zéro du compteur de restarts, relance.

        Différence avec restart : même si le service était en arrêt manuel ou
        en épuisement de restarts, on le relance avec un état vierge.
        """
        spec = self.manifests.get(name)
        if not spec:
            return {"status": "error", "error": f"service inconnu: {name}"}
        self.stop(name)
        self.manual_stop.discard(name)
        self.registry.set(name, {"pid": -1, "socket": "", "status": "running",
                                 "restarts": 0})
        sock = _resolve_socket(name, spec, self.registry)
        if self._launch(name, spec, sock):
            self.registry.save()
            return {"status": "ok", "pid": self.registry.get(name).get("pid"),
                    "socket": sock}
        return {"status": "error", "error": f"échec reset {name}"}

    def reset_all(self) -> dict:
        """Reset complet de tous les services gérés."""
        results = {}
        for name in list(self.registry.all().keys()):
            try:
                results[name] = self.reset(name).get("status", "error")
            except Exception as e:
                results[name] = "error"
        self.registry.save()
        return {"status": "ok", "results": results}

    def shutdown_all(self) -> dict:
        """Arrêt total DURABLE : stoppe tout et écrit un flag pour que le
        daemon ne relance PAS le superviseur (arrêt volontaire ≠ crash)."""
        try:
            (mw_home() / "supervisor.disabled").write_text("shutdown_all")
        except OSError:
            pass
        return self.shutdown()

    # ── Serveur socket ──

    def serve_socket(self):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            SUPERVISOR_SOCK.unlink(missing_ok=True)
        except OSError:
            pass
        self._sock.bind(str(SUPERVISOR_SOCK))
        os.chmod(str(SUPERVISOR_SOCK), 0o600)
        self._sock.listen(8)
        self._sock.settimeout(1.0)
        while self._running:
            try:
                conn, _ = self._sock.accept()
                threading.Thread(target=self._handle_conn, args=(conn,),
                                 daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_conn(self, conn: socket.socket):
        try:
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        req = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        resp = {"ok": False, "error": "invalid_json", "id": None}
                    else:
                        resp = self._dispatch(req)
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _dispatch(self, req: dict) -> dict:
        call = req.get("call", "")
        rid = req.get("id")
        try:
            if call == "ping":
                result = {"status": "ok", "pong": True,
                          "supervisor_pid": os.getpid()}
            elif call == "status":
                result = self.status()
            elif call == "list":
                result = self.list_services()
            elif call == "start":
                result = self.start(req.get("name", ""))
            elif call == "stop":
                result = self.stop(req.get("name", ""))
            elif call == "restart":
                result = self.restart(req.get("name", ""))
            elif call == "reset":
                result = self.reset(req.get("name", ""))
            elif call == "reset_all":
                result = self.reset_all()
            elif call == "shutdown":
                result = self.shutdown()
            elif call == "shutdown_all":
                result = self.shutdown_all()
            else:
                result = {"status": "error", "error": f"call inconnu: {call}"}
        except Exception as e:
            result = {"status": "error", "error": str(e)}
        return {"ok": result.get("status") in ("ok",), "result": result, "id": rid}


def _same_home(pid: int) -> bool:
    """Vrai si le process tourne avec le MÊME MODELWEAVER_HOME que nous.

    Le singleton superviseur (et l'adoption des services) est PAR HOME :
    plusieurs homes peuvent avoir chacun leur superviseur/daemon sans se
    bloquer (un ancien superviseur d'un home de test ne doit pas empêcher
    le superviseur d'un autre home de démarrer, ni adopter ses services)."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return True  # ne peut pas lire → conservateur (bloque le doublon)
    vals = {}
    for part in raw.split(b"\x00"):
        if not part:
            continue
        k, _, v = part.partition(b"=")
        vals[k.decode("utf-8", "replace")] = v.decode("utf-8", "replace")
    cand = vals.get("MODELWEAVER_HOME", "")
    ours = os.environ.get("MODELWEAVER_HOME", "")
    if cand and cand != ours:
        return False
    if not cand and not ours:
        return True
    if not cand:
        # candidat sur le home par défaut : même home seulement si nous
        # tournons aussi sur le home par défaut
        return ours == str(Path.home() / ".modelweaver")
    return True


def main():
    # Singleton STRICT : s'il y a déjà un superviseur vivant, on s'éteint.
    # (Le kill-and-replace de acquire_instance_lock laissait les services
    # déjà lancés en orphelins → doublons à chaque relance.)
    procs = _list_processes()
    for pid, cmdline in procs:
        if pid == os.getpid():
            continue
        if _match_service(cmdline, "supervisor") and _same_home(pid):
            print(f"[supervisor] un autre superviseur tourne déjà "
                  f"(pid {pid}) — arrêt.", file=sys.stderr)
            sys.exit(1)
    if not acquire_instance_lock("supervisor"):
        print("[supervisor] déjà en cours (lock supervisor)", file=sys.stderr)
        sys.exit(1)
    sup = Supervisor()
    sup.boot()

    def _on_term(signum, frame):
        # Arrêt propre : stoppe les services enfants avant de mourir,
        # sinon ils restent orphelins (→ doublons à la relance).
        try:
            sup.shutdown()
        except Exception:
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    threading.Thread(target=sup.serve_socket, daemon=True).start()
    print(f"[supervisor] prêt (pid {os.getpid()}, socket "
          f"{SUPERVISOR_SOCK})", flush=True)
    try:
        sup.supervise_loop()
    except KeyboardInterrupt:
        sup.shutdown()


if __name__ == "__main__":
    main()
