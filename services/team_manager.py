"""TeamManager — Registre global des équipes + cycle de vie.

Chaque équipe est définie par un .team.yaml et se compose :
  - D'un team_leader (agent orchestrateur, optionnel)
  - De membres (agents exécutants)
  - D'un workspace_id (optionnel, qui lie le director au workspace)

Le TeamManager garantit que tous les agents membres existent dans
agents.db et que le director est lié au workspace si workspace_id
est renseigné.
"""

from __future__ import annotations
import json
import threading
import time
from pathlib import Path
from typing import Any, List, Optional

from services.api.router import register_dynamic, unregister
from services.team_spec import TeamSpec, TeamMemberSpec, TeamLeaderSpec


# ── Helpers internes ──────────────────────────────────────────────

def _get_agent_db():
    from modules.sql.sql_module import AgentsDB
    return AgentsDB()


def _ensure_agent_exists(spec, role: str, occupation: str) -> int:
    """Crée l'agent dans agents.db s'il n'existe pas, retourne son agent_id."""
    db = _get_agent_db()
    row = db.conn.execute(
        "SELECT agent_id FROM agents WHERE name = ?", (spec.agent_name,)
    ).fetchone()
    if row:
        return row["agent_id"]

    config_json = json.dumps(spec.config or {})
    resources_json = json.dumps(spec.resources or {})

    if isinstance(spec, TeamLeaderSpec):
        effective_role = role
        effective_occupation = spec.occupation or occupation
    else:
        effective_role = spec.role or role
        effective_occupation = spec.occupation or occupation

    ref = f"agent:{spec.agent_name}"
    db.conn.execute("""
        INSERT INTO agents (name, ref, role_type, occupation, config_json, resources_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (spec.agent_name, ref, effective_role, effective_occupation,
          config_json, resources_json))
    db.conn.commit()

    row = db.conn.execute(
        "SELECT agent_id FROM agents WHERE name = ?", (spec.agent_name,)
    ).fetchone()
    return row["agent_id"]


def _set_workspace_director(workspace_id: str, director_agent_id: int):
    """Lie le director au workspace."""
    try:
        from modules.sql.workspace import WorkspaceDB
        wdb = WorkspaceDB()
        ws = wdb.workspaces.get(workspace_id)
        if not ws:
            wdb.workspaces.create(workspace_id, workspace_id,
                                  description=f"Workspace for team", director=director_agent_id)
        else:
            wdb.conn.execute(
                "UPDATE workspaces SET director = ? WHERE workspace_id = ?",
                (director_agent_id, workspace_id))
            wdb.conn.commit()
        wdb.close()
    except Exception:
        pass


# ── Team — instance runtime ──────────────────────────────────────

class Team:
    """Instance runtime d'une équipe."""

    def __init__(self, spec: TeamSpec):
        self.spec = spec
                self.team_leader_agent_id: Optional[int] = None

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def team_name(self) -> str:
        return self.spec.team_name

    # ── Setup ───────────────────────────────────────────────────

    def setup(self):
        """Assure que tous les agents existent et lie le leader au workspace."""
        # Director
        if self.spec.team_leader and self.spec.team_leader.agent_name:
            self.leader_agent_id = _ensure_agent_exists(
                self.spec.team_leader, "team_leader", "continue")
            if self.spec.workspace_id:
                _set_workspace_director(self.spec.workspace_id, self.team_leader_agent_id)

        # Members
        for m in self.spec.members:
            aid = _ensure_agent_exists(m, m.role, m.occupation)
            self.member_agent_ids[m.agent_name] = aid

        self.status = "ready"

    # ── Agent hydration helpers ─────────────────────────────────

    def _hydrate(self, agent_id: int):
        from services.agent_manager.service import AgentManager
        mgr = AgentManager(db=_get_agent_db())
        return mgr.hydrate(agent_id)

    def _chat_turn(self, agent_id: int, message: str, **kwargs) -> dict:
        agent = self._hydrate(agent_id)
        return agent.chat_turn(
            user_message=message,
            provider_ref=kwargs.get("provider_ref", ""),
            model_ref=kwargs.get("model_ref", ""),
        )

    def _execute(self, agent_id: int, request: str, entrypoint: str = "main", **kwargs) -> dict:
        agent = self._hydrate(agent_id)
        return agent.execute(
            request=request,
            provider_ref=kwargs.get("provider_ref", ""),
            model_ref=kwargs.get("model_ref", ""),
            entrypoint=entrypoint,
        )

    # ── Delegation ──────────────────────────────────────────────

    def delegate(self, request: str, entrypoint: str = "main",
                 target: Optional[str] = None, **kwargs) -> dict:
        """Délègue une requête à un membre. Si target est None et que le
        director existe, c'est le director qui reçoit la requête."""
        if target:
            aid = self.member_agent_ids.get(target)
            if not aid:
                return {"status": "error", "error": f"membre inconnu: {target}"}
        elif self.team_leader_agent_id:
            aid = self.team_leader_agent_id
        else:
            return {"status": "error", "error": "aucun director ni target spécifié"}

        return self._execute(aid, request, entrypoint, **kwargs)

    def chat(self, message: str, target: Optional[str] = None, **kwargs) -> dict:
        """Envoie un message de chat à un agent de l'équipe."""
        if target:
            aid = self.member_agent_ids.get(target)
            if not aid:
                return {"status": "error", "error": f"membre inconnu: {target}"}
        elif self.team_leader_agent_id:
            aid = self.team_leader_agent_id
        else:
            aid = next(iter(self.member_agent_ids.values()), None)
            if not aid:
                return {"status": "error", "error": "équipe vide"}

        return self._chat_turn(aid, message, **kwargs)

    # ── Lifecycle ───────────────────────────────────────────────

    def start(self, agent_name: Optional[str] = None):
        """Démarre un agent spécifique ou tous les agents de l'équipe."""
        if agent_name:
            self._set_agent_status(agent_name, "IDLE")
            return
        for name in self.member_agent_ids:
            self._set_agent_status(name, "IDLE")
        if self.team_leader_agent_id:
            self._set_agent_status(
                self.spec.team_leader.agent_name if self.spec.team_leader else "", "IDLE")
        self.status = "running"

    def stop(self, agent_name: Optional[str] = None):
        """Stoppe un agent spécifique ou tous les agents."""
        if agent_name:
            self._set_agent_status(agent_name, "STOPPED")
            return
        for name in self.member_agent_ids:
            self._set_agent_status(name, "STOPPED")
        if self.team_leader_agent_id:
            self._set_agent_status(
                self.spec.team_leader.agent_name if self.spec.team_leader else "", "STOPPED")
        self.status = "stopped"

    def restart(self):
        self.stop()
        self.start()

    def _set_agent_status(self, agent_name: str, status: str):
        db = _get_agent_db()
        db.conn.execute(
            "UPDATE agents SET status = ? WHERE name = ?", (status, agent_name))
        db.conn.commit()

    # ── Status / Health ─────────────────────────────────────────

    def status_info(self) -> dict:
        db = _get_agent_db()
        members_info = []
        for name, aid in self.member_agent_ids.items():
            row = db.conn.execute(
                "SELECT status, last_active_at FROM agents WHERE agent_id = ?",
                (aid,)).fetchone()
            members_info.append({
                "agent_name": name,
                "agent_id": aid,
                "status": row["status"] if row else "unknown",
                "last_active_at": row["last_active_at"] if row else None,
            })
        director_info = None
        if self.team_leader_agent_id:
            row = db.conn.execute(
                "SELECT status, last_active_at FROM agents WHERE agent_id = ?",
                (self.team_leader_agent_id,)).fetchone()
            director_info = {
                "agent_name": self.spec.team_leader.agent_name if self.spec.team_leader else "",
                "agent_id": self.team_leader_agent_id,
                "status": row["status"] if row else "unknown",
                "last_active_at": row["last_active_at"] if row else None,
            }
        return {
            "name": self.team_name,
            "status": self.status,
            "topology": self.spec.topology,
            "workspace_id": self.spec.workspace_id,
            "director": director_info,
            "members": members_info,
            "member_count": len(members_info),
        }

    def health(self) -> dict:
        ok = True
        issues = []
        for name, aid in self.member_agent_ids.items():
            row = _get_agent_db().conn.execute(
                "SELECT status FROM agents WHERE agent_id = ?", (aid,)).fetchone()
            if not row or row["status"] in ("STOPPED", "TERMINATED"):
                ok = False
                issues.append(f"{name}: {row['status'] if row else 'missing'}")
        return {"ok": ok, "issues": issues, "team": self.team_name}

    # ── Routes handlers ─────────────────────────────────────────

    def _status_handler(self, _params: dict) -> dict:
        return self.status_info()

    def _health_handler(self, _params: dict) -> dict:
        return self.health()

    def _start_handler(self, params: dict) -> dict:
        self.start(agent_name=params.get("agent_name"))
        return {"name": self.team_name, "status": "started"}

    def _stop_handler(self, params: dict) -> dict:
        self.stop(agent_name=params.get("agent_name"))
        return {"name": self.team_name, "status": "stopped"}

    def _restart_handler(self, _params: dict) -> dict:
        self.restart()
        return {"name": self.team_name, "status": "restarted"}

    def _delegate_handler(self, params: dict) -> dict:
        return self.delegate(
            request=params.get("request", ""),
            entrypoint=params.get("entrypoint", "main"),
            target=params.get("target"),
            provider_ref=params.get("provider_ref", ""),
            model_ref=params.get("model_ref", ""),
        )

    def _chat_handler(self, params: dict) -> dict:
        return self.chat(
            message=params.get("message", params.get("request", "")),
            target=params.get("target"),
            provider_ref=params.get("provider_ref", ""),
            model_ref=params.get("model_ref", ""),
        )

    def _routes_handler(self, _params: dict) -> dict:
        routes = sorted(self._routes)
        return {"name": self.team_name, "routes": routes, "count": len(routes)}

    # ── Route registration ──────────────────────────────────────

    TEAM_ROUTES = [
        "status", "health", "start", "stop", "restart",
        "delegate", "chat", "routes",
    ]

    def _register_routes(self):
        tn = self.team_name
        handler_map = {
            "status": self._status_handler,
            "health": self._health_handler,
            "start": self._start_handler,
            "stop": self._stop_handler,
            "restart": self._restart_handler,
            "delegate": self._delegate_handler,
            "chat": self._chat_handler,
            "routes": self._routes_handler,
        }
        for suffix, handler in handler_map.items():
            route = f"team/{tn}/{suffix}"
            register_dynamic(route, handler)
            self._routes.append(route)

    def _unregister_routes(self):
        for r in self._routes:
            try:
                unregister(r)
            except Exception:
                pass
        self._routes = []


# ── TeamManager — Singleton ──────────────────────────────────────

class TeamManager:
    """Registre global des équipes chargées depuis .team.yaml."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._teams: Dict[str, Team] = {}
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
    def teams(self) -> Dict[str, Team]:
        return self._teams

    def register(self, spec_or_path: TeamSpec | str | Path) -> Team:
        if isinstance(spec_or_path, (str, Path)):
            spec = TeamSpec.from_yaml(spec_or_path)
        else:
            spec = spec_or_path
        team = Team(spec)
        team.setup()
        team._register_routes()
        self._teams[spec.team_name] = team
        return team

    def unregister(self, team_name: str):
        team = self._teams.pop(team_name, None)
        if team:
            team._unregister_routes()

    def get(self, team_name: str) -> Optional[Team]:
        return self._teams.get(team_name)

    def list(self) -> List[dict]:
        return [t.status_info() for t in self._teams.values()]

    def start(self, team_name: str, agent_name: Optional[str] = None):
        team = self._teams.get(team_name)
        if team:
            team.start(agent_name=agent_name)

    def stop(self, team_name: str, agent_name: Optional[str] = None):
        team = self._teams.get(team_name)
        if team:
            team.stop(agent_name=agent_name)

    def restart(self, team_name: str):
        team = self._teams.get(team_name)
        if team:
            team.restart()

    def delegate(self, team_name: str, request: str,
                 entrypoint: str = "main", target: Optional[str] = None,
                 **kwargs) -> dict:
        team = self._teams.get(team_name)
        if not team:
            return {"status": "error", "error": f"team inconnue: {team_name}"}
        return team.delegate(request, entrypoint=entrypoint, target=target, **kwargs)

    def chat(self, team_name: str, message: str,
             target: Optional[str] = None, **kwargs) -> dict:
        team = self._teams.get(team_name)
        if not team:
            return {"status": "error", "error": f"team inconnue: {team_name}"}
        return team.chat(message, target=target, **kwargs)

    # ── Supervision ─────────────────────────────────────────────

    def supervise_teams(self):
        """Vérifie la santé de toutes les équipes."""
        for team in list(self._teams.values()):
            h = team.health()
            if not h["ok"]:
                pass  # log would go here

    def supervise_loop(self, interval: float = 15.0):
        """Boucle de supervision (thread daemon)."""
        def _loop():
            while True:
                try:
                    self.supervise_teams()
                except Exception:
                    pass
                time.sleep(interval)
        t = threading.Thread(target=_loop, daemon=True, name="team-mgr-supervisor")
        t.start()
        return t
