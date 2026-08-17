"""ServiceManager — Registre global des services + cycle de vie.

Point d'entrée unique pour :
  - Enregistrer un service depuis un .service.yaml
  - Démarrer/arrêter/redémarrer un service
  - Auto-enregistrer les routes d'entrypoints
  - Surveiller la santé
"""

from __future__ import annotations
import json
import os
import signal
import shlex
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from services._common import runtime_db_path
from services.api.router import register_dynamic, unregister
from services.service_spec import ServiceSpec


class Service:
    """Instance runtime d'un service défini par un ServiceSpec."""

    def __init__(self, spec: ServiceSpec):
        self.spec = spec
        self.process: Optional[subprocess.Popen] = None
        self.pid: int = -1
        self.status: str = "stopped"
        self.restarts: int = 0
        self.started_at: int = 0
        self._routes: List[str] = []

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def svc_name(self) -> str:
        return self.spec.svc_name

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self):
        if self.status == "running":
            return
        if self.spec.is_agent:
            self._start_agent()
        else:
            self._start_process()
        self._register_routes()

    def _start_agent(self):
        """In-process agent: just mark running, no subprocess."""
        self.pid = os.getpid()
        self.status = "running"
        self.started_at = int(time.time())
        self._write_db()

    def _start_process(self):
        """Spawn a subprocess for the service."""
        cmd_parts = []
        if self.spec.launch.command:
            cmd_parts = shlex.split(self.spec.launch.command)
            cmd_parts.extend(self.spec.launch.args)
        else:
            cmd_parts = [sys.executable, "-c", "pass"]
        try:
            proc = subprocess.Popen(
                cmd_parts,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=self.spec.launch.workdir or None,
            )
            self.process = proc
            self.pid = proc.pid
            self.status = "running"
            self.started_at = int(time.time())
            self._write_db()
        except Exception:
            self.status = "failed"
            self._write_db()

    def stop(self):
        if self.process:
            try:
                os.kill(self.pid, signal.SIGTERM)
                for _ in range(25):
                    try:
                        os.kill(self.pid, 0)
                        time.sleep(0.1)
                    except OSError:
                        break
                else:
                    os.kill(self.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            self.process = None
        self.pid = -1
        self.status = "stopped"
        self._unregister_routes()
        self._write_db()

    def restart(self):
        self.stop()
        self.start()

    def reload(self, reset_running: bool = True) -> dict:
        """RELOAD / REGEN_INLINE (Idée 18, O1/O6).

        Recharge le service (agent-as-service) depuis son manifest OUVERT :
          1. relit le manifest (fichier ouvert — les modifs externes restent
             ignorées, seules les mutations via ModelWeaver comptent) ;
          2. met à jour le spec en mémoire + config_json/workflow de l'agent BDD ;
          3. RESTART des entrypoints NON FINIS (reset state_json/variables du
             run en cours) + traitement des signaux en cours/non traités.
        Le "continue step-à-step" a été ABANDONNÉ (carnet O6) : changement de
        code → on redémarre les entrypoints concernés.
        Retourne {reloaded, changed, reason, restarted_entrypoints}.
        """
        if not self.spec.source_path:
            return {"status": "error",
                    "error": "pas de manifest source (spec construit en code)"}
        from services.manifest_store import open_manifest, close_manifest
        from services.service_spec import ServiceSpec
        # Re-charge depuis le DISQUE : on oublie le manifest mémorisé pour
        # ré-ouvrir (les modifs faites via ModelWeaver sont déjà écrites sur
        # disque ; les modifs externes sont prises en compte seulement ici,
        # au reload explicite — pas à chaque accès).
        close_manifest(self.spec.source_path)
        try:
            new_spec = ServiceSpec.from_yaml(self.spec.source_path)
        except Exception as e:
            return {"status": "error", "error": f"rechargement manifest: {e}"}
        changed = new_spec.to_yaml_dict() != self.spec.to_yaml_dict()
        old_spec = self.spec
        self.spec = new_spec
        self._reload_agent_bdd(new_spec)
        restarted = []
        if changed and reset_running:
            restarted = self._restart_unfinished_entrypoints()
        self._reload_signals()
        return {
            "status": "ok",
            "reloaded": True,
            "changed": changed,
            "reason": "workflow/entrypoints rechargés depuis le manifest" if changed
                      else "aucun changement détecté",
            "restarted_entrypoints": restarted,
            "previous_spec_name": old_spec.name,
        }

    def _reload_agent_bdd(self, spec: ServiceSpec) -> None:
        """Met à jour config_json de l'agent BDD (workflow/entrypoints) depuis
        le manifest rechargé."""
        if not spec.is_agent or not spec.agent:
            return
        try:
            from modules.sql.sql_module import AgentsDB
            from modules.sql.agents_repo import _add_column_if_missing  # noqa
            db = AgentsDB()
            row = db.conn.execute(
                "SELECT agent_id FROM agents WHERE name = ?", (spec.name,)).fetchone()
            if not row:
                return
            config = dict(spec.agent.config or {})
            # Entrypoints du manifest → config.entrypoints (pour le FSM).
            if spec.entrypoints:
                config["entrypoints"] = {
                    ep_name: {"type": ep.type, "description": ep.description,
                              "method": ep.method, "path": ep.path,
                              "handler": ep.handler, "entrypoint": ep.entrypoint}
                    for ep_name, ep in spec.entrypoints.items()
                }
            db.conn.execute(
                "UPDATE agents SET config_json = ? WHERE agent_id = ?",
                (__import__("json").dumps(config), row["agent_id"]))
            db.conn.commit()
        except Exception:
            pass

    def _restart_unfinished_entrypoints(self) -> List[str]:
        """RESTART des entrypoints non finis (le run est en cours : on reset
        l'état du run — state_json + variables — mais on ne tue pas l'agent).
        Vérifie la terminaison via la stack (agent_runtime.current_step)."""
        if not self.spec.is_agent:
            return []
        restarted: List[str] = []
        try:
            from modules.sql.sql_module import AgentsDB
            db = AgentsDB()
            row = db.conn.execute(
                "SELECT agent_id, state_json FROM agents WHERE name = ?",
                (self.name,)).fetchone()
            if not row:
                return []
            agent_id = row["agent_id"]
            # Entrypoint en cours (stack) : agent_runtime.current_step non null
            # → le run n'est PAS terminé.
            rt = db.conn.execute(
                "SELECT current_step FROM agent_runtime WHERE agent_id = ?",
                (agent_id,)).fetchone()
            cur_step = rt["current_step"] if rt else None
            if cur_step:
                ep_name = "main"
                try:
                    st = __import__("json").loads(row["state_json"] or "{}")
                    ep_name = st.get("current_entrypoint", "main")
                except Exception:
                    pass
                # Reset de l'état du run en cours (on repart au début).
                db.conn.execute(
                    "UPDATE agents SET state_json = ?, variables_json = ? "
                    "WHERE agent_id = ?",
                    (__import__("json").dumps({}), __import__("json").dumps({}),
                     agent_id))
                db.conn.execute(
                    "UPDATE agent_runtime SET current_step = NULL "
                    "WHERE agent_id = ?", (agent_id,))
                db.conn.commit()
                restarted.append(ep_name)
            return restarted
        except Exception:
            return restarted

    def _reload_signals(self) -> None:
        """Traite les signaux en cours/non traités au reload : les signaux
        PENDING (jamais ACKED) restent — l'agent les reprendra à son réveil ;
        les signaux en cours de traitement (ACKED non COMPLETED) sont remis à
        PENDING pour être re-traités après le reload."""
        try:
            from modules.sql.sql_module import AgentsDB
            db = AgentsDB()
            row = db.conn.execute(
                "SELECT agent_id FROM agents WHERE name = ?", (self.name,)).fetchone()
            if not row:
                return
            agent_id = row["agent_id"]
            db.conn.execute(
                "UPDATE agent_signals SET status = 'PENDING' "
                "WHERE agent_id = ? AND status = 'ACKED'", (agent_id,))
            db.conn.commit()
        except Exception:
            pass

    def is_alive(self) -> bool:
        if self.pid <= 0:
            return False
        try:
            os.kill(self.pid, 0)
        except OSError:
            return False
        try:
            with open(f"/proc/{self.pid}/stat") as f:
                state = f.read().split()[2]
                if state == 'Z':
                    return False
        except (OSError, IndexError):
            pass
        return True

    def health(self) -> dict:
        h = self.spec.health
        if h.type == "pid":
            return {"ok": self.is_alive(), "pid": self.pid}
        if h.type == "http":
            try:
                import urllib.request
                url = f"http://127.0.0.1:{h.port}{h.endpoint}"
                resp = urllib.request.urlopen(url, timeout=h.timeout)
                return {"ok": resp.status == 200, "status": resp.status}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": True}

    # ── Routes ───────────────────────────────────────────────────────────

    LIFECYCLE_ROUTES = [
        "health", "status", "log", "start", "stop", "restart", "routes"
    ]

    def _register_routes(self):
        """Auto-enregistre les routes entrypoint + cycle de vie obligatoires."""
        sn = self.svc_name

        # Entrypoints du manifest
        for ep_name, ep_spec in self.spec.entrypoints.items():
            route = f"service/{sn}/{ep_name}"
            handler = self._make_handler(ep_spec)
            register_dynamic(route, handler)
            self._routes.append(route)

        # Routes obligatoires
        mandatory = [
            ("health",  lambda p, _s=self: _s._health_handler(p)),
            ("status",  lambda p, _s=self: _s._status_handler(p)),
            ("log",     lambda p, _s=self: _s._log_handler(p)),
            ("start",   lambda p, _s=self: _s._start_handler(p)),
            ("stop",    lambda p, _s=self: _s._stop_handler(p)),
            ("restart", lambda p, _s=self: _s._restart_handler(p)),
            ("reload",  lambda p, _s=self: _s._reload_handler(p)),
            ("routes",  lambda p, _n=sn: _s._routes_handler(p, _n)),
        ]
        for suffix, handler in mandatory:
            route = f"service/{sn}/{suffix}"
            if route not in self._routes:  # ne pas écraser un entrypoint nommé pareil
                register_dynamic(route, handler)
                self._routes.append(route)

    def _unregister_routes(self):
        for r in self._routes:
            try:
                unregister(r)
            except Exception:
                pass
        self._routes = []

    def _make_handler(self, ep: "EntrypointSpec"):
        """Crée un handler de route pour un entrypoint."""
        from services.api._shared import _get_bridge, _get_cat
        if ep.type == "agent-chat":
            return lambda p, _n=self.name: self._handle_chat(p)
        if ep.type == "agent-execute":
            return lambda p, _n=self.name, _ep=ep.entrypoint: self._handle_execute(p, _ep)
        if ep.type == "inline" and ep.handler:
            code = compile(ep.handler, f"<entrypoint:{ep.description}>", "exec")
            ns: dict = {}
            exec(code, ns)
            fn = ns.get("handler")
            if fn:
                return fn
        return lambda p: {"error": f"unknown entrypoint type: {ep.type}"}

    def _handle_chat(self, params: dict) -> dict:
        from services.agent_manager.service import AgentManager
        from modules.sql.sql_module import AgentsDB
        mgr = AgentManager(db=AgentsDB())
        agent_row = mgr.db.conn.execute(
            "SELECT agent_id FROM agents WHERE name=?", (self.name,)
        ).fetchone()
        if not agent_row:
            return {"status": "error", "error": f"agent {self.name} not found"}
        agent = mgr.hydrate(agent_row["agent_id"])
        result = agent.chat_turn(
            user_message=params.get("message", params.get("request", "")),
            provider_ref=params.get("provider_ref", ""),
            model_ref=params.get("model_ref", ""),
        )
        return result

    def _handle_execute(self, params: dict, entrypoint: str = "main") -> dict:
        from services.agent_manager.service import AgentManager
        from modules.sql.sql_module import AgentsDB
        mgr = AgentManager(db=AgentsDB())
        agent_row = mgr.db.conn.execute(
            "SELECT agent_id FROM agents WHERE name=?", (self.name,)
        ).fetchone()
        if not agent_row:
            return {"status": "error", "error": f"agent {self.name} not found"}
        agent = mgr.hydrate(agent_row["agent_id"])
        result = agent.execute(
            request=params.get("request", params.get("message", "")),
            provider_ref=params.get("provider_ref", ""),
            model_ref=params.get("model_ref", ""),
            entrypoint=entrypoint,
        )
        return result

    def _log_path(self) -> Optional[Path]:
        from services._common import mw_home
        return mw_home() / "logs" / f"{self.name}.log"

    def _health_handler(self, _params: dict) -> dict:
        h = self.health()
        h["name"] = self.svc_name
        h["mode"] = self.spec.launch.mode
        h["uptime"] = int(time.time()) - self.started_at if self.started_at else 0
        return h

    def _log_handler(self, params: dict) -> dict:
        log_path = self._log_path()
        if not log_path or not log_path.exists():
            return {"name": self.svc_name, "log": "", "lines": 0}
        try:
            tail = params.get("tail", 50)
            text = log_path.read_text(errors="replace")
            lines = text.splitlines()
            tailed = lines[-int(tail):] if tail else lines
            return {"name": self.svc_name, "log": "\n".join(tailed), "lines": len(tailed), "total": len(lines)}
        except Exception as e:
            return {"error": str(e)}

    def _start_handler(self, _params: dict) -> dict:
        self.start()
        return {"name": self.svc_name, "status": self.status, "pid": self.pid}

    def _stop_handler(self, _params: dict) -> dict:
        self.stop()
        return {"name": self.svc_name, "status": self.status}

    def _restart_handler(self, _params: dict) -> dict:
        self.restart()
        return {"name": self.svc_name, "status": self.status, "pid": self.pid}

    def _routes_handler(self, params: dict, svc_name: str) -> dict:
        from services.api.router import ROUTES, STREAMING_ROUTES
        routes = []
        for r in ROUTES:
            if r.startswith(f"service/{svc_name}/") or r == f"service/{svc_name}":
                routes.append(r)
        for r in STREAMING_ROUTES:
            if r.startswith(f"service/{svc_name}/") or r == f"service/{svc_name}":
                routes.append(r + " (streaming)")
        routes.sort()
        return {"name": svc_name, "routes": routes, "count": len(routes)}

    def _status_handler(self, _params: dict) -> dict:
        return {
            "name": self.svc_name,
            "status": self.status,
            "pid": self.pid,
            "restarts": self.restarts,
            "running": self.is_alive(),
            "spec": self.spec.to_dict(),
        }

    def _reload_handler(self, params: dict) -> dict:
        """Route service/{name}/reload — voir Service.reload."""
        return self.reload(reset_running=bool(params.get("reset_running", True)))

    # ── DB sync ──────────────────────────────────────────────────────────

    def _write_db(self):
        try:
            conn = sqlite3.connect(str(runtime_db_path()))
            conn.execute(
                "INSERT OR REPLACE INTO services "
                "(name, mode, command, args, status, pid, parent, restart, restarts, started_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (self.svc_name, self.spec.launch.mode,
                 self.spec.launch.command, " ".join(self.spec.launch.args),
                 self.status, self.pid, self.spec.parent,
                 1 if self.spec.supervisor.restart else 0,
                 self.restarts, self.started_at)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


class ServiceManager:
    """Registre global des services chargés depuis .service.yaml."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._services: Dict[str, Service] = {}
                    cls._instance._initialized = False
        return cls._instance

    def init(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._initialized = True

    @property
    def services(self) -> Dict[str, Service]:
        return self._services

    def register(self, spec_or_path: ServiceSpec | str | Path, start: bool = True) -> Service:
        if isinstance(spec_or_path, (str, Path)):
            spec = ServiceSpec.from_yaml(spec_or_path)
        else:
            spec = spec_or_path
        svc = Service(spec)
        self._services[spec.svc_name] = svc
        if start:
            svc.start()
        else:
            svc._register_routes()  # routes only, no process spawn
        return svc

    def unregister(self, svc_name: str):
        svc = self._services.pop(svc_name, None)
        if svc:
            svc.stop()

    def get(self, svc_name: str) -> Optional[Service]:
        return self._services.get(svc_name)

    def list(self) -> List[dict]:
        return [svc._status_handler({}) for svc in self._services.values()]

    def start(self, svc_name: str):
        svc = self._services.get(svc_name)
        if svc:
            svc.start()

    def stop(self, svc_name: str):
        svc = self._services.get(svc_name)
        if svc:
            svc.stop()

    def restart(self, svc_name: str):
        svc = self._services.get(svc_name)
        if svc:
            svc.restart()

    def reload(self, svc_name: str, reset_running: bool = True) -> dict:
        """Reload/regen_inline d'un service (agent). Voir Service.reload."""
        svc = self._services.get(svc_name)
        if not svc:
            return {"status": "error", "error": f"service inconnu: {svc_name}"}
        return svc.reload(reset_running=reset_running)

    # ── Supervision ──────────────────────────────────────────────────────

    def supervise_services(self):
        """Vérifie tous les services et relance les morts (si restart=True)."""
        for svc in list(self._services.values()):
            if svc.status != "running":
                continue
            if not svc.is_alive():
                if svc.spec.supervisor.restart:
                    max_r = svc.spec.supervisor.max_restarts
                    if max_r > 0 and svc.restarts >= max_r:
                        svc.status = "exhausted"
                        svc._write_db()
                        continue
                    svc.restarts += 1
                    svc.status = "restarting"
                    svc._write_db()
                    svc.start()
                else:
                    svc.status = "crashed"
                    svc._write_db()

    def supervise_loop(self, interval: float = 5.0):
        """Boucle de supervision (thread daemon)."""
        def _loop():
            while True:
                try:
                    self.supervise_services()
                except Exception:
                    pass
                time.sleep(interval)
        t = threading.Thread(target=_loop, daemon=True, name="svc-mgr-supervisor")
        t.start()
        return t
