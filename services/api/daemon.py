#!/usr/bin/env python3
"""
ModelWeaver — Daemon API locale.

Backend unique, indépendant de toute GUI. Expose sur 127.0.0.1 (HTTP/JSON) les
opérations « accessibles depuis l'externe » définies dans ARCHITECTURE_API.md.
Toute interface (GUI Tauri v1/v2, CLI, web, TUI) est un simple client.

Sécurité :
  - bind STRICT sur 127.0.0.1 (jamais 0.0.0.0)
  - token de session écrit dans ~/.modelweaver/api.token (perms 600), exigé via
    l'en-tête `Authorization: Bearer <token>` pour toute route /v1/*.

Découverte par les clients :
  - ~/.modelweaver/api.port  : port courant
  - ~/.modelweaver/api.token : token de session

Usage:
  python mw_daemon.py [--port 8770]
"""
import sys
import os
import json
import time
import secrets
import argparse
import platform
import contextlib
import threading
import sys
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# Ancrage du dépôt sur sys.path (modules/, services/, sql/ à la racine) AVANT
# tout import de services.* — indispensable quand le daemon est lancé directement
# (ex. supervisé par Rust : `python services/api/daemon.py serve`).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._common import mw_home
from services._common import _db_paths, log_to_file, runtime_db_path
from services.installer_worker import jobs

from services.api.router import ROUTES, STREAMING_ROUTES
from services.api._shared import (
    API_VERSION, MW_VERSION,
    _mw_dir, _get_mw, _get_cat, _get_rt, _get_km, _DB_LOCK,
)
from AgentFrameWork.router import capabilities_catalog as router_capabilities

# Handlers : s'enregistrent automatiquement via register() à l'import
import services.api.catalogue_api
import services.api.catalogue_agents
import services.api.handlers
from services.api.handlers.agents import op_agent_list, op_agent_create


def _process_install_jobs():
    """Process one queued install job (blocking). Called by the background thread."""
    rt = _get_rt()
    # Phase 1: pick a job and mark running (atomically under lock)
    _DB_LOCK.acquire()
    try:
        cur = rt.conn.execute(
            "SELECT id, ref, name, job_type FROM install_jobs WHERE status='queued' ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if not row:
            return
        jid, ref, name, job_type = row
        rt.conn.execute("UPDATE install_jobs SET status='running', updated_at=strftime('%s','now') WHERE id=?",
                    (jid,))
        rt.conn.commit()
    finally:
        _DB_LOCK.release()
    cat_singleton = _get_cat()
    mw = _get_mw()
    # Phase 2: run the install (no lock — may take minutes)
    try:
        if job_type == "install":
            result = jobs.install_tool(ref, mw_shared=mw, cat_shared=cat_singleton)
        elif job_type == "uninstall":
            result = jobs.uninstall_tool(ref, mw_shared=mw, cat_shared=cat_singleton)
        else:
            result = {"status": "error", "error": f"unknown job_type: {job_type}"}
        status = "installed" if result.get("status") == "ok" else "failed"
        log = json.dumps(result)
    except Exception as e:
        result = {"status": "error", "error": str(e)}
        status = "failed"
        log = str(e)[:500]
    # Phase 3: update job status (brief lock)
    _DB_LOCK.acquire()
    try:
        rt.conn.execute("UPDATE install_jobs SET status=?, log=?, updated_at=strftime('%s','now') WHERE id=?",
                    (status, log[:500], jid))
        rt.conn.commit()
    finally:
        _DB_LOCK.release()


def _job_processor_loop(interval: float = 5.0):
    """Background thread : consume la queue install_jobs."""
    def _loop():
        while True:
            try:
                _process_install_jobs()
            except Exception:
                pass
            time.sleep(interval)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


_collector_proc = None


def _rollback_shared_dbs():
    """Rollback best-effort des connexions partagées (mw/cat/rt).

    Un handler en échec au milieu d'écritures laisse une transaction
    implicite OUVERTE sur le singleton (sqlite3 isolation_level="") :
    le lock d'écriture WAL reste tenu par le daemon et tout écrivain
    externe échoue avec « database is locked ». Appelé sur tout code
    HTTP >= 400 avant l'envoi de la réponse.
    """
    for getter in ("_get_mw", "_get_cat", "_get_rt"):
        try:
            db = globals().get(getter)()
            db.conn.rollback()
        except Exception:
            pass


def _collector_pidfile() -> Path:
    return _mw_dir() / "run" / "usage_collector.pid"


def _kill_old_collector():
    """Tue le rassembleur d'usage précédent (même fichier PID) s'il est encore
    vivant. Évite l'accumulation de processes orphelins entre redémarrages."""
    pidfile = _collector_pidfile()
    if not pidfile.exists():
        return
    try:
        old_pid = int(pidfile.read_text().strip())
        import subprocess
        # Vérifie que le PID correspond bien à usage_collector.py
        try:
            import os, signal
            with open(f"/proc/{old_pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace")
            if "usage_collector" not in cmdline:
                return  # PID recyclé, ne pas tuer
        except OSError:
            return  # déjà mort
        os.kill(old_pid, signal.SIGTERM)
        for _ in range(50):
            try:
                os.kill(old_pid, 0)
                time.sleep(0.1)
            except OSError:
                return  # mort
        os.kill(old_pid, signal.SIGKILL)
    except (ValueError, OSError):
        pass
    finally:
        try:
            pidfile.unlink(missing_ok=True)
        except OSError:
            pass


def _start_usage_collector(log=None):
    """Lance le rassembleur d'usage en process séparé (start_new_session),
    isolé d'un crash du daemon. Tue tout collecteur précédent."""
    global _collector_proc
    try:
        import subprocess
        script = Path(__file__).resolve().parent.parent.parent / "modules" / "usage" / "usage_collector.py"
        if not script.exists():
            return
        # Tue l'ancien collecteur avant d'en spawner un nouveau
        _kill_old_collector()
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _collector_proc = proc
        _collector_pidfile().parent.mkdir(parents=True, exist_ok=True)
        _collector_pidfile().write_text(str(proc.pid))
        if log is not None:
            log.info("Rassembleur d'usage démarré", pid=proc.pid)
    except Exception as e:
        if log is not None:
            log.warning("Lancement rassembleur d'usage échoué", error=str(e))


def _supervise_usage_collector(log=None):
    """Relance le rassembleur d'usage s'il est mort (crash hors reboot daemon)."""
    global _collector_proc
    try:
        if _collector_proc is None:
            _start_usage_collector(log)
            return
        if _collector_proc.poll() is not None:
            if log is not None:
                log.warning("Rassembleur d'usage mort, relance",
                            code=_collector_proc.returncode)
            _collector_proc = None
            _start_usage_collector(log)
    except Exception:
        pass


def _collector_supervisor_loop(interval: float = 30.0):
    """Boucle de supervision du rassembleur d'usage (thread daemon)."""
    import time
    try:
        from services.logger import MWLogger
        log = MWLogger("daemon")
    except Exception:
        log = None
    while True:
        try:
            _supervise_usage_collector(log)
        except Exception:
            pass
        time.sleep(interval)


_model_sync_proc = None


def _model_sync_pidfile() -> Path:
    return _mw_dir() / "run" / "model_sync.pid"


def _kill_old_model_sync():
    """Tue le synchroniseur de modèles précédent (même fichier PID) s'il est
    encore vivant. Évite l'accumulation de processes orphelins."""
    pidfile = _model_sync_pidfile()
    if not pidfile.exists():
        return
    try:
        old_pid = int(pidfile.read_text().strip())
        import subprocess
        try:
            import os, signal
            with open(f"/proc/{old_pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode("utf-8", errors="replace")
            if "model_sync" not in cmdline:
                return  # PID recyclé, ne pas tuer
        except OSError:
            return  # déjà mort
        os.kill(old_pid, signal.SIGTERM)
        for _ in range(50):
            try:
                os.kill(old_pid, 0)
                time.sleep(0.1)
            except OSError:
                return  # mort
        os.kill(old_pid, signal.SIGKILL)
    except (ValueError, OSError):
        pass
    finally:
        try:
            pidfile.unlink(missing_ok=True)
        except OSError:
            pass


def _start_model_sync(log=None):
    """Lance le synchroniseur de modèles (process séparé, 1 cycle/heure).

    Si le superviseur est disponible, on lui délègue (il est la seule source
    de vérité) — sinon fallback local (daemon autonome).
    """
    global _model_sync_proc
    try:
        if _delegate_to_supervisor("model-sync"):
            return
        import subprocess
        script = (Path(__file__).resolve().parent.parent.parent
                  / "services" / "model_sync" / "model_sync.py")
        if not script.exists():
            return
        _kill_old_model_sync()
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _model_sync_proc = proc
        _model_sync_pidfile().parent.mkdir(parents=True, exist_ok=True)
        _model_sync_pidfile().write_text(str(proc.pid))
        if log is not None:
            log.info("Synchroniseur de modèles démarré", pid=proc.pid)
    except Exception as e:
        if log is not None:
            log.warning("Lancement synchroniseur de modèles échoué", error=str(e))


def _supervise_model_sync(log=None):
    """Relance le synchroniseur de modèles s'il est mort (crash hors reboot)."""
    global _model_sync_proc
    try:
        if _model_sync_proc is None:
            _start_model_sync(log)
            return
        if _model_sync_proc.poll() is not None:
            if log is not None:
                log.warning("Synchroniseur de modèles mort, relance",
                            code=_model_sync_proc.returncode)
            _model_sync_proc = None
            _start_model_sync(log)
    except Exception:
        pass


def _delegate_to_supervisor(service: str) -> bool:
    """Si le superviseur tourne, lui délègue le lancement de `service`.

    Retourne True si délégué (le superviseur gère), False si on doit lancer
    nous-même (superviseur absent → daemon autonome).
    """
    try:
        from services.supervisor.client import get_supervisor_client
        client = get_supervisor_client()
        if not client.ping():
            return False
        resp = client.start(service)
        return resp.get("status") == "ok"
    except Exception:
        return False


def _start_supervisor():
    """Lance le superviseur de services en process séparé (si absent)."""
    try:
        from services.supervisor.client import get_supervisor_client
        if get_supervisor_client().ping():
            return True
        import subprocess
        script = (Path(__file__).resolve().parent.parent.parent
                  / "services" / "supervisor" / "main.py")
        if not script.exists():
            return False
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent.parent)
        subprocess.Popen([sys.executable, str(script)],
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=env)
        return True
    except Exception:
        return False


def _supervise_supervisor_loop(interval: float = 10.0):
    """Surveille le superviseur de services ; le relance s'il meurt.

    Supervision mutuelle : le superviseur lance/gère le daemon, et le daemon
    relance le superviseur s'il disparaît (crash hors reboot).
    """
    import time
    try:
        from services.logger import MWLogger
        log = MWLogger("daemon")
    except Exception:
        log = None
    while True:
        try:
            from services._common import mw_home
            if not (mw_home() / "supervisor.disabled").exists():
                from services.supervisor.client import get_supervisor_client
                if not get_supervisor_client().ping():
                    log.warning("Superviseur absent, relance") if log else None
                    _start_supervisor()
        except Exception:
            pass
        time.sleep(interval)


def _model_sync_supervisor_loop(interval: float = 120.0):
    """Boucle de supervision du synchroniseur de modèles (thread daemon)."""
    import time
    try:
        from services.logger import MWLogger
        log = MWLogger("daemon")
    except Exception:
        log = None
    while True:
        try:
            _supervise_model_sync(log)
        except Exception:
            pass
        time.sleep(interval)


def _agent_dynamic_route(method: str, parts: List[str], params: dict):
    """Route dynamique `agents/{id}/{sub}` — proxy vers AFD (ou fallback local).

    Si l'AFD est joignable (socket Unix), tous les appels agents passent par
    le processus dédié. Sinon, fallback local (mono-process).
    """
    if len(parts) < 3 or parts[0] != "agents":
        return None
    try:
        agent_id = int(parts[1])
    except ValueError:
        return {"code": 400, "payload": {"error": "bad_agent_id", "agent_id": parts[1]}}
    sub = parts[2]

    # ── Storage (V0.6.8) : reste ici (infra, pas agent) ──
    if sub == "storage":
        return _storage_route(agent_id, method, parts[3:], params)

    # ── Budget (V0.6.11) : interrogation du budget fournisseur/modèle ──
    if sub == "budget":
        return _budget_route(agent_id, method, parts[3:], params)

    # ── FsAuth (V0.6.20) : allowlist d'accès hôte absolu ──
    if sub == "fs_auth":
        return _fs_auth_route(agent_id, method, parts[3:], params)

    if sub == "routes":
        if method != "GET":
            return {"code": 405, "payload": {"error": "method_not_allowed", "method": method}}
        from services.api.afd_client import get_afd_client
        routes = get_afd_client()._local_routes_for(agent_id)
        return {"code": 200, "payload": {
            "agent_id": agent_id,
            "routes": routes,
        }}

    # Résoudre et exécuter via le proxy AFD (socket Unix ou fallback local)
    from services.api.afd_client import get_afd_client
    result = get_afd_client().call(agent_id, sub, **params)
    if result.get("status") == "error" and "code" in result:
        code = result["code"]
        reason = result.get("reason", "unknown")
        return {"code": code, "payload": {"error": "op_not_allowed", "op": sub, "reason": reason}}
    code = 200 if result.get("status") in ("ok", "success") else 500
    return {"code": code, "payload": result}


def _log_dynamic_route(method: str, parts: List[str], params: dict):
    """Route dynamique `log/<name>/<action>` (et `log/list`).

    Action : write | get | tail | list | clear | info. Le canal est
    ~/.modelweaver/logs/<name>.log (service services.logs).
    """
    if not parts or parts[0] != "log":
        return None
    from services.logs import service as logs
    if len(parts) == 1 or (len(parts) == 2 and parts[1] == "list"):
        # log/list → tous les canaux
        return {"code": 200, "payload": {"status": "ok", "channels": logs.list_channels()}}
    name = parts[1]
    action = parts[2] if len(parts) > 2 else "write"
    if action == "write":
        res = logs.write(name, params.get("level", "INFO"),
                         params.get("message", ""), params.get("events"))
        return {"code": 200, "payload": res}
    if action == "get":
        return {"code": 200, "payload": logs.get(name, params.get("max_lines"))}
    if action == "tail":
        return {"code": 200, "payload": logs.tail(name, params.get("lines", 100))}
    if action == "clear":
        return {"code": 200, "payload": logs.clear(name)}
    if action == "info":
        return {"code": 200, "payload": logs.info(name)}
    return {"code": 400, "payload": {"error": "unknown_log_action", "action": action}}


def _storage_route(agent_id: int, method: str, sub_parts: List[str], params: dict) -> dict:
    """Gère les sous-routes agents/{id}/storage/* (infra, pas agent)."""
    from AgentFrameWork.agent_storage import AgentStorage
    from modules.sql.sql_module import AgentsDB
    st = AgentStorage(agent_id, AgentsDB().conn)
    if sub_parts and sub_parts[0] == "quota" and len(sub_parts) >= 2 and sub_parts[1] == "approve":
        if method != "POST":
            return {"code": 405, "payload": {"error": "method_not_allowed", "method": method}}
        new_max = params.get("max_bytes")
        if not new_max:
            return {"code": 400, "payload": {"error": "max_bytes requis"}}
        st.approve_quota_request(int(new_max))
        from services.audit import audit
        audit("storage.quota.approve", agent_id=agent_id, new_max=int(new_max), ok=True)
        return {"code": 200, "payload": {"status": "ok", "agent_id": agent_id,
                                          "max_bytes": st.max_bytes,
                                          "used_bytes": st.used_bytes,
                                          "quota_request": None}}
    if method == "POST":
        st.recalc_used()
    return {"code": 200, "payload": {
        "agent_id": agent_id,
        "max_bytes": st.max_bytes,
        "used_bytes": st.used_bytes,
        "quota_request": st.quota_request(),
    }}


def _budget_route(agent_id: int, method: str, sub_parts: List[str], params: dict) -> dict:
    """agents/{id}/budget — interroge le budget restant du fournisseur/modèle de l'agent."""
    if sub_parts:
        return {"code": 404, "payload": {"error": "not_found"}}
    from modules.sql.sql_module import AgentsDB
    row = AgentsDB().conn.execute(
        "SELECT config_json FROM agents WHERE agent_id=?",
        (agent_id,)
    ).fetchone()
    if not row:
        return {"code": 404, "payload": {"error": "agent_not_found"}}
    config = json.loads(row["config_json"] or "{}")
    provider_ref = config.get("provider_ref") or ""
    model_ref = config.get("model_ref") or ""
    if not provider_ref or not model_ref:
        return {"code": 200, "payload": {"agent_id": agent_id, "budget": {}}}
    from services.tarif import check_budget, _get as _get_tarif
    _get_tarif().seed_default()
    budget = check_budget(provider_ref, model_ref)
    return {"code": 200, "payload": {"agent_id": agent_id, "provider_ref": provider_ref,
                                      "model_ref": model_ref, "budget": budget}}


def _fs_auth_route(agent_id: int, method: str, sub_parts: List[str], params: dict) -> dict:
    """agents/{id}/fs_auth — gestion de l'allowlist d'accès hôte (FsAuthManager).

    GET    agents/{id}/fs_auth          -> liste des racines autorisées
    POST   agents/{id}/fs_auth          -> grant (root_path, mode=r|rw)
    DELETE agents/{id}/fs_auth          -> revoke (root_path)
    """
    from services.fs_auth import FsAuthManager
    try:
        mgr = FsAuthManager()
    except Exception as e:
        return {"code": 500, "payload": {"error": f"fs_auth indispo: {e}"}}
    try:
        if method == "GET":
            return {"code": 200, "payload": {"agent_id": agent_id, "grants": mgr.list(agent_id)}}
        if method == "POST":
            root = params.get("root_path")
            mode = params.get("mode", "r")
            if not root:
                return {"code": 400, "payload": {"error": "root_path requis"}}
            mgr.grant(agent_id, root, mode)
            return {"code": 200, "payload": {"status": "ok", "agent_id": agent_id,
                                             "root_path": os.path.abspath(root),
                                             "mode": "rw" if mode == "rw" else "r"}}
        if method == "DELETE":
            root = params.get("root_path")
            if not root:
                return {"code": 400, "payload": {"error": "root_path requis"}}
            mgr.revoke(agent_id, root)
            return {"code": 200, "payload": {"status": "ok", "agent_id": agent_id,
                                             "root_path": os.path.abspath(root),
                                             "revoked": True}}
        return {"code": 405, "payload": {"error": "method_not_allowed", "method": method}}
    finally:
        mgr.close()


def _restart_all_service() -> None:
    """Relance TOUS les services + le daemon (recharge le code Python).

    - arrête les services à tick (file_watcher, petri_runtime) ;
    - arrête le superviseur de services ;
    - re-exécute le daemon avec les MÊMES args (os.execv) → tous les modules
      sont re-importés frais (fsm_interpreter, agents, skills, taskflow…).
    Le superviseur Rust (kill-and-replace) relance aussi le daemon si l'execv
    échoue — double sécurité.
    """
    from services.logger import MWLogger
    log = MWLogger("daemon")
    log.info("restart_all_service : relance du daemon")
    # Arrête les services à tick (file_watcher, petri_runtime).
    try:
        from services.service_ticker import ServiceTicker
        st = ServiceTicker()
        st.stop()
    except Exception as e:
        log.warning("ticker stop échoué", error=str(e))
    # Re-exec du daemon avec les mêmes arguments (recharge tous les modules).
    try:
        argv = sys.argv if sys.argv and sys.argv[0] else ["python3"]
        os.execv(sys.executable, [sys.executable] + argv)
    except Exception as e:
        log.error("execv échoué, sortie", error=str(e))
        sys.exit(1)


class MWAPIHandler(BaseHTTPRequestHandler):
    server_version = "ModelWeaverDaemon/1.0"

    def log_message(self, *args):
        pass  # silence le logging par défaut sur stderr

    def _send(self, code, payload):
        # Ferme toute transaction implicite laissée ouverte par un handler en
        # échec (sinon le lock d'écriture SQLite reste tenu par le daemon et
        # bloque tous les écrivains externes — "database is locked").
        if code >= 400:
            _rollback_shared_dbs()
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # CORS : la webview Tauri (http://localhost:5173 en dev) fait des fetch
        # directs vers le daemon (bridge.ts daemonPost).
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # Preflight CORS (les fetch avec Authorization déclenchent un preflight).
        origin = self.headers.get("Origin")
        self.send_response(204)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _authorized(self):
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        return secrets.compare_digest(auth, expected)

    def _serve_panel_js(self, pid: str):
        """Sert un panel compilé (module ES) pour import() dynamique.

        Chemins : ~/.modelweaver/panels-dist/<id>.js. Content-Type JS + CORS.
        Cas spéciaux :
          - panels/file/_shared/react : module qui ré-exporte window.React
            (React PARTAGÉ — les panels externes l'importent via alias).
        """
        try:
            from services._common import mw_home
            if pid == "_shared/react":
                body = b'const React = window.React; export default React; export const { useEffect, useState, useCallback, useMemo, useRef } = React; export const jsx = React.createElement; export const jsxs = React.createElement; export const Fragment = React.Fragment;\n'
                self._send_js(body)
                return
            if pid == "_shared/react-dom":
                body = b'const ReactDOM = window.ReactDOM; export default ReactDOM;\n'
                self._send_js(body)
                return
            f = mw_home() / "panels-dist" / f"{pid}.js"
            if not f.exists():
                self._send(404, {"error": "panel not found", "id": pid})
                return
            self._send_js(f.read_bytes())
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _send_js(self, body: bytes):
        """Envoie du JS (module ES) avec Content-Type + CORS."""
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript")
        self.send_header("Content-Length", str(len(body)))
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "version": MW_VERSION, "api": API_VERSION})
            return
        prefix = f"/{API_VERSION}/"
        if not self.path.startswith(prefix):
            self._send(404, {"error": "not_found", "path": self.path})
            return
        # Les JS compilés des panels sont servis SANS auth : import() dynamique
        # du navigateur ne peut pas envoyer le header Authorization (ni le token
        # en query). Ce sont des modules publics (pas de secret dedans).
        route_early = self.path[len(prefix):].strip("/")
        if route_early.startswith("panels/file/"):
            self._serve_panel_js(route_early[len("panels/file/"):])
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        route = self.path[len(prefix):].strip("/")
        parts = [p for p in route.split("/") if p]
        # Rate limiting
        client_ip = self.client_address[0]
        try:
            from services.ratelimit import check_rate_limit, RemoteConnectionDenied
            check_rate_limit(route, client_ip)
        except RemoteConnectionDenied as e:
            self._send(403, {"error": "forbidden", "detail": str(e)})
            return
        except Exception as e:
            self._send(429, {"error": "rate_limited", "detail": str(e)})
            return
        # Route dynamique agents/{id}/routes ?
        dyn = _agent_dynamic_route("GET", parts, {})
        if dyn is not None:
            self._send(dyn["code"], {"ok": dyn["code"] == 200,
                                     "route": route, "result": dyn["payload"]})
            return
        # Logs : log/<name>/<action>
        dyn = _log_dynamic_route("GET", parts, {})
        if dyn is not None:
            self._send(dyn["code"], {"ok": dyn["code"] == 200,
                                     "route": route, "result": dyn["payload"]})
            return
        # Catalogue des capacités (rôles/skills) ?
        if route == "capabilities":
            self._send(200, {"ok": True, "route": route, "result": router_capabilities()})
            return
        # Panels GUI : servir le JS compilé (modules ES) pour import() dynamique
        if route.startswith("panels/file/"):
            self._serve_panel_js(route[len("panels/file/"):])
            return
        # Routes statiques enregistrées (system/info, etc.)
        handler = ROUTES.get(route)
        if handler:
            result = handler({})
            self._send(200, {"ok": True, "route": route, "result": result})
            return
        self._send(404, {"error": "not_found", "path": self.path})

    def _handle_stream(self, route: str, handler, params: dict):
        """SSE : envoie les headers puis délègue l'itération au handler(stream, params, wfile)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        # CORS : la webview Tauri (Origin localhost:517x / http://tauri.localhost)
        # lit le flux SSE via fetch → comme _send(), on renvoie l'Origin et on
        # autorise les cross-origin. Sans ça le navigateur bloque la lecture du
        # stream et le client GUI reçoit une erreur immédiate (« ✕ Erreur »).
        origin = self.headers.get("Origin")
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")
        self.end_headers()
        try:
            handler(params, self.wfile)
        except Exception as e:
            import traceback
            err = json.dumps({"error": str(e), "trace": traceback.format_exc()})
            self.wfile.write(f"event: error\ndata: {err}\n\n".encode())
            self.wfile.write(b"event: done\ndata: {\"done\":true}\n\n")
        finally:
            try:
                self.wfile.flush()
            except Exception:
                pass
            self.close_connection = True

    def do_POST(self):
        prefix = f"/{API_VERSION}/"
        if not self.path.startswith(prefix):
            self._send(404, {"error": "not_found", "path": self.path})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        route = self.path[len(prefix):].strip("/")
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            params = json.loads(raw) if raw else {}
        except Exception as e:
            self._send(400, {"error": "bad_request", "detail": str(e)})
            return
        # Rate limiting
        client_ip = self.client_address[0]
        try:
            from services.ratelimit import check_rate_limit, RemoteConnectionDenied
            check_rate_limit(route, client_ip)
        except RemoteConnectionDenied as e:
            self._send(403, {"error": "forbidden", "detail": str(e)})
            return
        except Exception as e:
            self._send(429, {"error": "rate_limited", "detail": str(e)})
            return
        # Streaming SSE ?
        stream_handler = STREAMING_ROUTES.get(route)
        if stream_handler:
            self._handle_stream(route, stream_handler, params)
            return
        # Restart TOUS les services + le daemon (recharge le code — les
        # modifications de fsm_interpreter/agents/etc. sont relues).
        if route == "restart_all_service":
            self._send(200, {"ok": True, "route": route,
                             "result": {"status": "restarting",
                                        "note": "relance du daemon (os.execv)"}})
            threading.Timer(0.5, _restart_all_service).start()
            return
        # Route dynamique agents/{id}/{op} ?
        parts = [p for p in route.split("/") if p]
        dyn = _agent_dynamic_route("POST", parts, params)
        if dyn is not None:
            self._send(dyn["code"], {"ok": dyn["code"] == 200,
                                     "route": route, "result": dyn["payload"]})
            return
        # Logs : log/<name>/<action>
        dyn = _log_dynamic_route("POST", parts, params)
        if dyn is not None:
            self._send(dyn["code"], {"ok": dyn["code"] == 200,
                                     "route": route, "result": dyn["payload"]})
            return
        # JSON normal
        handler = ROUTES.get(route)
        if not handler:
            self._send(404, {"error": "unknown_route", "route": route})
            return
        try:
            result = handler(params)
            # Endpoint OpenAI-compatible : le payload /chat/completions et
            # /responses est renvoyé BRUT (pas enveloppé dans {ok, route,
            # result}) pour que les SDK de benchmark (Inspect, Promptfoo) le
            # parsent directement. Un wrapper {ok, route, result} casse le
            # parse (response.output → None → TypeError).
            if route in ("chat/completions", "responses"):
                self._send(200, result)
                return
            self._send(200, {"ok": True, "route": route, "result": result})
        except Exception as e:
            import traceback
            self._send(500, {"ok": False, "route": route, "error": str(e),
                             "trace": traceback.format_exc()})

    def do_DELETE(self):
        prefix = f"/{API_VERSION}/"
        if not self.path.startswith(prefix):
            self._send(404, {"error": "not_found", "path": self.path})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        route = self.path[len(prefix):].strip("/")
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            params = json.loads(raw) if raw else {}
        except Exception as e:
            self._send(400, {"error": "bad_request", "detail": str(e)})
            return
        parts = [p for p in route.split("/") if p]
        dyn = _agent_dynamic_route("DELETE", parts, params)
        if dyn is not None:
            self._send(dyn["code"], {"ok": dyn["code"] == 200,
                                     "route": route, "result": dyn["payload"]})
            return
        self._send(404, {"error": "unknown_route", "route": route})


def serve(port: int = 8770, bind: str = "127.0.0.1") -> None:
    """Point d'entrée du service `api` (supervisé). Un seul daemon à la fois."""
    from services._common import acquire_instance_lock
    if not acquire_instance_lock("api"):
        print("❌ daemon déjà en cours (lock api)", file=sys.stderr)
        sys.exit(1)

    from services.logger import MWLogger
    log = MWLogger("daemon")

    mw = _mw_dir()
    token_file = mw / "api.token"
    # Réutiliser le token existant : le lock (kill-and-replace) relance le daemon
    # fréquemment ; régénérer le token à chaque redémarrage invaliderait les
    # clients (Rust watch thread, frontend) qui l'ont déjà lu → 401 en boucle.
    token = token_file.read_text().strip() if token_file.exists() else None
    if not token or len(token) != 64:
        token = secrets.token_hex(32)
        token_file.write_text(token)
        os.chmod(token_file, 0o600)
    else:
        os.chmod(token_file, 0o600)

    # Tokens de domaine (défense d'app) : générés au premier boot,
    # vérifiés à CHAQUE démarrage. Un mismatch = le domaine est désactivé
    # (toutes ses écritures refusées) + alerte au boot.
    try:
        from services.domain_access import init_tokens, verify_tokens
        init_tokens()
        vt = verify_tokens()
        for dom in vt.get("failed", []):
            log.warning("Domaine désactivé (token mismatch/absent)",
                        domaine=dom)
        if not vt.get("ok"):
            log.warning("Vérification des tokens de domaine incomplète",
                        failed=vt.get("failed"))
        log.info("Tokens de domaine vérifiés", domains=len(vt.get("domains", [])))
    except Exception as e:
        log.warning("Vérification tokens de domaine ignorée", error=str(e))

    # Onboard des clés API depuis .env → key_manager sqlite (keys.db).
    try:
        from modules.sqlite.keys.onboard import onboard_from_env
        _onb = onboard_from_env()
        log.info("Clés API onboardées depuis .env",
                 loaded=len(_onb.get("loaded", [])), existing=_onb.get("existing"))
    except Exception as _e:
        log.warning("Onboarding clés .env échoué", error=str(_e))

    # bind avec retry (port occupé au boot)
    server = None
    for attempt in range(10):
        try:
            server = ThreadingHTTPServer((bind, port), MWAPIHandler)
            break
        except OSError as e:
            log.warning("Bind échoué", port=port, attempt=attempt+1, error=str(e))
            time.sleep(1)
    if server is None:
        log.critical("Impossible de binder le daemon", port=port)
        sys.exit(1)

    server.token = token
    (mw / "api.port").write_text(str(port))

    # Initialiser les connexions partagées AVANT le processeur de jobs.
    mw_singleton = _get_mw()
    cat_singleton = _get_cat()
    rt_singleton = _get_rt()  # crée services + service_commands tables si absentes
    # Charger les clés en mémoire (après validation keyring OS).
    try:
        _get_km().load()
    except Exception as e:
        log.warning("Keyring indisponible", error=str(e))

    # Onboarder : import automatique des clés API depuis .env dans le KeyManager.
    # Les providers déjà présents (keyring) ne sont pas écrasés.
    try:
        from modules.key_manager.onboarder import Onboarder
        env_path = _REPO_ROOT / ".env"
        if env_path.exists():
            count = Onboarder(_get_km()).onboard_from_env(env_path)
            if count:
                log.info("Clés .env onboardées dans KeyManager", count=count)
    except Exception as e:
        log.warning("Onboarder .env échoué", error=str(e))

    # busy_timeout 30s pour les opérations concurrentes en arrière-plan
    mw_singleton.conn.execute("PRAGMA busy_timeout = 30000")

    # Au démarrage : si la table locale des outils installés est vide,
    # lancer une détection une fois. Les outils installés hors flux catalogue
    # (ex. dépendances système installées via `deps/install`) doivent
    # apparaître immédiatement dans la GUI sans attendre un install/uninstall.
    try:
        if len(mw_singleton.local_tools.list_all()) == 0:
            mw_singleton.scan_installed_tools()
            mw_singleton.commit()
    except Exception as e:
        log.warning("Scan outils installés échoué", error=str(e))

    # Injecter la connexion partagée dans le module jobs (évite les locks).
    jobs.set_shared_conn(mw_singleton.conn)

    # Démarre le processeur de jobs en arrière-plan (consomme install_jobs).
    _job_processor_loop(interval=3.0)

    # Démarre le rassembleur d'usage (process séparé, isolé d'un crash du
    # daemon). Collecte le journal disque -> SQLite de façon asynchrone.
    # Best-effort : si le lancement échoue, le logging disque continue
    # (les données seront consolidées au prochain lancement).
    _start_usage_collector(log)

    # Supervise le rassembleur : le relance s'il meurt (crash hors reboot).
    _supervisor = threading.Thread(
        target=_collector_supervisor_loop, args=(30.0,), daemon=True)
    _supervisor.start()

    # Démarre le synchroniseur de modèles (process séparé, 1 cycle/heure).
    # Interroge les APIs des clés, met à jour provider_models_mapping et gère
    # le backoff/defunct via model_probe_state. Best-effort au boot.
    _start_model_sync(log)

    # Supervise le synchroniseur de modèles : relance s'il meurt.
    _model_sync_supervisor = threading.Thread(
        target=_model_sync_supervisor_loop, args=(120.0,), daemon=True)
    _model_sync_supervisor.start()

    # Surveille le superviseur de services : s'il meurt, le daemon le relance
    # (supervision mutuelle : le superviseur gère le daemon, le daemon gère le
    # superviseur). Le superviseur est la source de vérité pour lancer/arrêter
    # les services ; le daemon le relance s'il disparaît.
    _sup_supervisor = threading.Thread(
        target=_supervise_supervisor_loop, args=(10.0,), daemon=True)
    _sup_supervisor.start()

    # Service supervisor : lit service_commands, monitore et relance les services.
    from services.service_manager import ServiceManager
    ServiceManager().supervise_loop(interval=5.0)

    # Team supervisor : surveille la santé des équipes.
    from services.team_manager import TeamManager
    TeamManager().supervise_loop(interval=15.0)

    # Service ticker : lance les services à tick (file_watcher, usage_tick...)
    # en singleton (threads éphémères vérifiés ou permanents 1s).
    _start_service_ticker(log)

    # Registre des services système + état runtime global (services.db /
    # runtime.db) : daemon, supervisor_general, ticker_general, watcher,
    # sqlite… avec pid/port du process courant.
    try:
        from services.system_registry import register_system_services
        register_system_services(pid=os.getpid(), port=port,
                                 mw_version=MW_VERSION, log=log)
    except Exception as e:
        if log:
            log.warning("system_registry non initialisé", error=str(e))

    # Activer le StreamBus cross-process (partagé avec l'AFD si démarré)
    try:
        from AgentFrameWork.stream_bus import activate_cross_process, resolve_stream_path
        activate_cross_process(resolve_stream_path())
    except Exception as e:
        log.warning("StreamBus cross-process échoué", error=str(e))

    log.info("Daemon démarré", port=port, api=API_VERSION, version=MW_VERSION,
             token=str(token_file), routes=len(ROUTES))
    # Boot agents : crée les agents système s'ils n'existent pas encore.
    _ensure_boot_agents(log)

    # Boot teams : charge les manifests .team.yaml
    _ensure_teams(log)

    # Auto-découverte des modules/routes → modules.db (route /v1/infra/modules).
    try:
        from modules.sqlite.modules import db as M_DB, write as M_W
        mdb = M_DB()
        res = M_W.discover(mdb)
        mdb.close()
        log.info("Modules découverts", count=res.get("total", 0),
                 loaded=res.get("loaded", 0))
    except Exception as _e:
        log.warning("discover modules échoué", error=str(_e))

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Arrêt du daemon")


# Agents système créés au démarrage si absents.
BOOT_AGENTS = [
    {"role": "chat", "name": "installer", "occupation": "continue",
     "config": {"description": "Agent de chat pour la fenêtre installateur/dashboard"}},
    {"role": "chat", "name": "sandbox", "occupation": "continue",
     "config": {"description": "Agent de chat pour la fenêtre sandbox IDE"}},
]

# Fichiers .service.yaml — tous les services connus du système
MANIFESTS_DIR = Path(__file__).resolve().parent.parent.parent / "services" / "manifests"
SERVICE_MANIFESTS = sorted(MANIFESTS_DIR.glob("*.service.yaml"))

# Fichiers .team.yaml — toutes les équipes
TEAM_MANIFESTS = sorted((MANIFESTS_DIR / "teams").glob("*.team.yaml"))


def _start_service_ticker(log=None) -> None:
    """Démarre le ServiceTicker (threads éphémères singleton) et y branche les
    services à tick de fond :
      - file_watcher : vérification des fichiers sources + propagation stale ;
      - usage_tick   : agrégation d'usage (si disponible).
    Rythmes : file_watcher ~60s (éphémère), usage 1s (permanent).
    """
    from services.service_ticker import ServiceTicker
    st = ServiceTicker()

    # reprend les services persistés d'un précédent démarrage (redémarrage)
    try:
        n = st.load_persisted()
        if n and log:
            log.info("ServiceTicker: %d services persistés relancés", n)
    except Exception:
        pass

    # file_watcher : service éphémère (60s) — mtime/hash + propagation stale.
    try:
        from services.file_watcher import run_service as fw_tick
        st.register("file_watcher", interval_s=60, fn=fw_tick,
                    cmd="services.file_watcher:run_service")
    except Exception as e:
        if log:
            log.warning("file_watcher non enregistré", error=str(e))

    # petri_runtime : miroir temps réel du FSM (observation pure, 10s).
    try:
        from services.petri_runtime import run_service as pr_tick
        st.register("petri_runtime", interval_s=10, fn=pr_tick,
                    cmd="services.petri_runtime:run_service")
    except Exception as e:
        if log:
            log.warning("petri_runtime non enregistré", error=str(e))

    # budget_cost_interrogator : interrogation systémique coûts/quotas/keys (60s).
    try:
        from services.budget_cost_interrogator import interrogate as bci_tick
        st.register("budget_cost_interrogator", interval_s=60, fn=bci_tick,
                    cmd="services.budget_cost_interrogator:interrogate")
    except Exception as e:
        if log:
            log.warning("budget_cost_interrogator non enregistré", error=str(e))

    # budget_guess_analyst : ajuste les guess de QUOTA (5 min).
    try:
        from services.budget_guess_analyst import analyze as bga_tick
        st.register("budget_guess_analyst", interval_s=300, fn=bga_tick,
                    cmd="services.budget_guess_analyst:analyze")
    except Exception as e:
        if log:
            log.warning("budget_guess_analyst non enregistré", error=str(e))

    # cost_guess_analyst : ajuste les guess de COUT (5 min).
    try:
        from services.cost_guess_analyst import analyze as cga_tick
        st.register("cost_guess_analyst", interval_s=300, fn=cga_tick,
                    cmd="services.cost_guess_analyst:analyze")
    except Exception as e:
        if log:
            log.warning("cost_guess_analyst non enregistré", error=str(e))

    # score_experience_manager : ajuste les scores par expérience (60s).
    try:
        from services.score_experience_manager import analyze as sem_tick
        st.register("score_experience_manager", interval_s=60, fn=sem_tick,
                    cmd="services.score_experience_manager:analyze")
    except Exception as e:
        if log:
            log.warning("score_experience_manager non enregistré", error=str(e))

    st.start()
    if log:
        log.info("ServiceTicker démarré")


def _ensure_boot_agents(log):
    """Crée les BOOT_AGENTS dans agents.db et enregistre les manifests service.

    Seuls les manifests avec parent='agent-as-service' sont auto-démarrés
    par le ServiceManager Python. Les services système (catalogue, installer,
    api, tester) sont gérés par le superviseur Rust et ignorés ici.
    """
    from services.service_manager import ServiceManager
    import sqlite3
    mgr = ServiceManager()

    # 1. Créer les agents dans agents.db si absents
    existing = op_agent_list({})
    existing_names = {a["name"] for a in existing.get("agents", [])}
    for spec in BOOT_AGENTS:
        name = spec.get("name", "")
        if not name:
            continue
        if name in existing_names:
            log.info("Boot agent déjà présent", name=name)
        else:
            try:
                result = op_agent_create(spec)
                if result.get("status") == "ok":
                    log.info("Boot agent créé", name=name, agent_id=result.get("agent_id"))
                else:
                    log.warning("Boot agent échoué", name=name, error=result.get("error"))
            except Exception as e:
                log.error("Boot agent erreur", name=name, error=str(e))

    # 2. Charger tous les manifests .service.yaml
    #    Les services système (Rust) sont enregistrés sans être démarrés.
    #    Les agent-as-service sont enregistrés ET démarrés.
    for mpath in SERVICE_MANIFESTS:
        if not mpath.exists():
            continue
        try:
            from services.service_spec import ServiceSpec
            spec = ServiceSpec.from_yaml(mpath)
            start = spec.parent == "agent-as-service"
            mgr.register(spec, start=start)
            log.info("Service manifest chargé", name=spec.name, mode=spec.launch.mode,
                     parent=spec.parent or "system", started=start)
        except Exception as e:
            log.warning("Service manifest échoué", path=str(mpath), error=str(e))


def _ensure_teams(log):
    """Charge tous les manifests .team.yaml dans le TeamManager.

    Les équipes sont enregistrées avec leurs agents et leurs routes.
    Les agents membres et le director sont créés dans agents.db si absents.
    Le workspace director est lié si workspace_id est spécifié.
    """
    if not TEAM_MANIFESTS:
        log.info("Aucun manifest .team.yaml trouvé", path=str(MANIFESTS_DIR / "teams"))
        return
    from services.team_manager import TeamManager
    mgr = TeamManager()
    for mpath in TEAM_MANIFESTS:
        if not mpath.exists():
            continue
        try:
            mgr.register(mpath)
            log.info("Team manifest chargé", name=mpath.stem)
        except Exception as e:
            log.warning("Team manifest échoué", path=str(mpath), error=str(e))


def main():
    # Charger .env (racine du projet) pour exposer les clés API aux agents
    # (le process daemon en setsid n'hérite pas des clés du shell parent).
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    # Alias de clés : le .env utilise GOOGLE_GEMINI_API_KEY, mais litellm
    # (et les bridges) lisent GOOGLE_API_KEY / GEMINI_API_KEY.
    for src, dst in (("GOOGLE_GEMINI_API_KEY", "GOOGLE_API_KEY"),
                     ("GOOGLE_GEMINI_API_KEY", "GEMINI_API_KEY")):
        if not os.environ.get(dst) and os.environ.get(src):
            os.environ[dst] = os.environ[src]

    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", nargs="?", default="serve",
                        help="commande (serve par défaut)")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--bind", default="127.0.0.1",
                        help="adresse de bind (127.0.0.1 par défaut ; 0.0.0.0 pour le mode Docker)")
    args = parser.parse_args()
    # `serve` est la commande par défaut ; tout autre argument positionnel
    # inconnu est ignoré (compatibilité avec les superviseurs qui le passent).
    if args.cmd not in ("serve", None):
        pass
    serve(args.port, bind=args.bind)


if __name__ == "__main__":
    main()
