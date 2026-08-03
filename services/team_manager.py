"""TeamManager — Registre global des équipes + cycle de vie.

Chaque équipe est définie par un .team.yaml et se compose :
  - D'un team_leader (agent orchestrateur, optionnel)
  - De membres (agents exécutants)
  - D'un workspace_id (optionnel, qui lie le team_leader au workspace)

Le TeamManager garantit que tous les agents membres existent dans
agents.db et que le team_leader est lié au workspace si workspace_id
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


def _ensure_agent_exists(spec, role: str, occupation: str,
                         team_name: str = "") -> int:
    """Crée l'agent dans agents.db s'il n'existe pas, retourne son agent_id.

    Le nom réel de l'agent est préfixé par le team_name pour garantir
    l'unicité inter-projets. Un agent_name 'lead-bug-hunter' dans l'équipe
    'bug-busters' devient 'bug-busters/lead-bug-hunter'.
    """
    db = _get_agent_db()
    scoped_name = f"{team_name}/{spec.agent_name}" if team_name else spec.agent_name

    resources = dict(spec.resources or {})
    if spec.provider_ref:
        resources.setdefault("llm_pref", {})["provider"] = spec.provider_ref
    if spec.model_ref:
        resources.setdefault("llm_pref", {})["model"] = spec.model_ref
    resources_json = json.dumps(resources)

    row = db.conn.execute(
        "SELECT agent_id, resources_json FROM agents WHERE name = ?", (scoped_name,)
    ).fetchone()
    if row:
        # Toujours réécrire resources_json : le manifest est la source de
        # vérité (retire les anciennes préférences provider/model si le
        # spec n'en définit plus → allocation automatique par le LLMManager).
        updates = ["resources_json = ?"]
        params = [resources_json]
        # Mettre à jour config_json si le spec définit un config (workflow…)
        if spec.config:
            updates.append("config_json = ?")
            params.append(json.dumps(spec.config or {}))
        if updates:
            db.conn.execute(
                f"UPDATE agents SET {', '.join(updates)} WHERE agent_id = ?",
                (*params, row["agent_id"]))
            db.conn.commit()
        return row["agent_id"]

    config_json = json.dumps(spec.config or {})
    # Si le membre ne définit pas de workflow explicite, charger le .agent.yaml
    # du rôle (ex. codeur → codeur@v2.agent.yaml, greedy). Permet de réutiliser
    # la même déclaration d'agent pour plusieurs membres (codeur-a, codeur-b…).
    if not spec.config:
        from services.api.catalogue_agents import _load_agent_yaml_config
        loaded = _load_agent_yaml_config(spec.role, spec.agent_name)
        if loaded:
            config_json = json.dumps(loaded)

    if isinstance(spec, TeamLeaderSpec):
        effective_role = role
        effective_occupation = spec.occupation or occupation
    else:
        effective_role = spec.role or role
        effective_occupation = spec.occupation or occupation

    ref = f"agent:{scoped_name}"
    db.conn.execute("""
        INSERT INTO agents (name, ref, role_type, occupation, config_json, resources_json)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (scoped_name, ref, effective_role, effective_occupation,
          config_json, resources_json))
    db.conn.commit()

    row = db.conn.execute(
        "SELECT agent_id FROM agents WHERE name = ?", (scoped_name,)
    ).fetchone()
    return row["agent_id"]


def _set_workspace_director(workspace_id: str, team_name: str):
    """Lie le workspace à l'équipe (team_name), pas à un agent particulier.
    Le leader actuel est résolu dynamiquement par project.py."""
    try:
        from modules.sql.workspace import WorkspaceDB
        wdb = WorkspaceDB()
        ws_row = wdb.workspaces.get(workspace_id)
        if not ws_row:
            wdb.workspaces.create(workspace_id, workspace_id,
                                  description=f"Workspace for {team_name}",
                                  director=team_name)
        else:
            wdb.conn.execute(
                "UPDATE workspaces SET director = ? WHERE workspace_id = ?",
                (team_name, workspace_id))
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
        self.member_agent_ids: Dict[str, int] = {}
        self.status: str = "stopped"
        self._routes: List[str] = []

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def team_name(self) -> str:
        return self.spec.team_name

    # ── Setup ───────────────────────────────────────────────────

    def setup(self):
        """Assure que tous les agents existent et lie le leader au workspace."""
        # Leader
        if self.spec.team_leader and self.spec.team_leader.agent_name:
            self.team_leader_agent_id = _ensure_agent_exists(
                self.spec.team_leader, "team_leader", "continue",
                team_name=self.spec.team_name)
            if self.spec.workspace_id:
                _set_workspace_director(self.spec.workspace_id, self.spec.team_name)
            self._seed_leader_workflow()

        # Members
        for m in self.spec.members:
            aid = _ensure_agent_exists(m, m.role, m.occupation,
                                       team_name=self.spec.team_name)
            self.member_agent_ids[m.agent_name] = aid

        # Lier le workspace à la team (flat ou leader-driven) : les agents du
        # swarm en déduisent le project_id git (repo central de référence).
        if self.spec.workspace_id and not self.spec.team_leader:
            _set_workspace_director(self.spec.workspace_id, self.spec.team_name)

        self.status = "ready"

    def _seed_leader_workflow(self):
        """Génère un workflow FSM pour le team_leader : appelle chaque
        membre via `agent_call` séquentiellement. Les résultats sont
        accessibles dans les variables `_member_0`, `_member_1`, ...

        Le leader transmet le workspace_id (si présent) + la requête
        originale aux workers, qui bouclent en greedy sur la file de
        tâches du workspace.""" 
        member_names = [f"{self.team_name}/{m.agent_name}" for m in self.spec.members]
        if not member_names or not self.team_leader_agent_id:
            return

        steps = []
        for i, mname in enumerate(member_names):
            step_id = f"call_{i}"
            step_inputs = {"request": "{{request}}"}
            if self.spec.workspace_id:
                step_inputs["workspace_id"] = self.spec.workspace_id
            steps.append({
                "type": "agent_call",
                "id": step_id,
                "agent": mname,
                "entrypoint": "main",
                "inputs": step_inputs,
                "capture": {"content": f"_member_{i}"},
                "next": "done" if i == len(member_names) - 1 else f"call_{i+1}",
            })
        steps.append({"type": "end", "id": "done", "status": "SUCCESS"})
        workflow = {"steps": steps}

        db = _get_agent_db()
        db.conn.execute(
            "UPDATE agents SET config_json = ? WHERE agent_id = ?",
            (json.dumps({"role": "team_leader", "workflow": workflow}),
             self.team_leader_agent_id),
        )
        db.conn.commit()

    # ── Agent hydration helpers ─────────────────────────────────

    def _hydrate(self, agent_id: int):
        from services.agent_manager.service import Agent
        return Agent.hydrate(agent_id, db=_get_agent_db())

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
        """Délègue une requête à un membre ou au team_leader.

        Si target est None et que le team_leader existe, c'est le team_leader
        qui traite la requête (scatter-gather via son workflow FSM)."""
        if target:
            aid = self.member_agent_ids.get(target)
            if not aid and self.spec.team_leader and target == self.spec.team_leader.agent_name:
                aid = self.team_leader_agent_id
            if not aid:
                return {"status": "error", "error": f"membre inconnu: {target}"}
        elif self.team_leader_agent_id:
            aid = self.team_leader_agent_id
        else:
            return {"status": "error", "error": "aucun team_leader ni target spécifié"}

        return self._execute(aid, request, entrypoint, **kwargs)

    def chat(self, message: str, target: Optional[str] = None, **kwargs) -> dict:
        """Envoie un message de chat à un agent de l'équipe."""
        if target:
            aid = self.member_agent_ids.get(target)
            if not aid and self.spec.team_leader and target == self.spec.team_leader.agent_name:
                aid = self.team_leader_agent_id
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
        scoped = f"{self.team_name}/{agent_name}" if self.team_name else agent_name
        db.conn.execute(
            "UPDATE agents SET status = ? WHERE name = ?", (status, scoped))
        db.conn.commit()

    # ── Dynamic member management ───────────────────────────────

    def add_member(self, agent_name: str, role: str,
                   occupation: str = "noncontinue",
                   provider_ref: str = "", model_ref: str = "") -> dict:
        """Ajoute un membre à l'équipe à chaud (BDD + runtime)."""
        spec = TeamMemberSpec(
            agent_name=agent_name,
            role=role,
            occupation=occupation,
            provider_ref=provider_ref,
            model_ref=model_ref,
        )
        aid = _ensure_agent_exists(spec, role, occupation,
                                   team_name=self.spec.team_name)
        self.member_agent_ids[agent_name] = aid
        self._seed_leader_workflow()
        return {"status": "ok", "agent_id": aid, "agent_name": agent_name}

    def set_leader(self, agent_name: str, role: str,
                   occupation: str = "continue",
                   provider_ref: str = "", model_ref: str = "") -> dict:
        """Définit ou remplace le team_leader à chaud."""
        leader_spec = TeamLeaderSpec(
            agent_name=agent_name,
            role=role,
            occupation=occupation,
            workflow="orchestrate",
            provider_ref=provider_ref,
            model_ref=model_ref,
        )
        self.spec.team_leader = leader_spec
        aid = _ensure_agent_exists(leader_spec, "team_leader", occupation,
                                   team_name=self.spec.team_name)
        self.team_leader_agent_id = aid
        self._seed_leader_workflow()
        if self.spec.workspace_id:
            _set_workspace_director(self.spec.workspace_id, self.spec.team_name)
        return {"status": "ok", "agent_id": aid, "agent_name": agent_name}

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
        leader_info = None
        if self.team_leader_agent_id:
            row = db.conn.execute(
                "SELECT status, last_active_at FROM agents WHERE agent_id = ?",
                (self.team_leader_agent_id,)).fetchone()
            leader_info = {
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
            "team_leader": leader_info,
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

    def add_member(self, team_name: str, agent_name: str, role: str,
                   occupation: str = "noncontinue",
                   provider_ref: str = "", model_ref: str = "") -> dict:
        team = self._teams.get(team_name)
        if not team:
            return {"status": "error", "error": f"team inconnue: {team_name}"}
        return team.add_member(agent_name, role, occupation,
                                provider_ref=provider_ref, model_ref=model_ref)

    def set_leader(self, team_name: str, agent_name: str, role: str,
                   occupation: str = "continue",
                   provider_ref: str = "", model_ref: str = "") -> dict:
        team = self._teams.get(team_name)
        if not team:
            return {"status": "error", "error": f"team inconnue: {team_name}"}
        return team.set_leader(agent_name, role, occupation,
                                provider_ref=provider_ref, model_ref=model_ref)

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
