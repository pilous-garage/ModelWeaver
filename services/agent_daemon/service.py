"""AgentDaemon — Interface unique pour interagir avec les agents.

Usage:
    from services.agent_daemon import AgentDaemon

    result = AgentDaemon.call("assistant_1", "chat", message="hello")
    result = AgentDaemon.call(42, "status")
    result = AgentDaemon.call("assistant_1", "pause")

L'appelant (gateway HTTP, CLI, autre service) ne sait rien de l'implémentation
derrière : résolution agent, routage rôle, exécution FSM.
"""

import json
from typing import Any, Dict, List, Optional

from modules.sql.db import AgentsDB
from modules.llm_manager.litellm_bridge import LiteLLMBridge
from modules.llm_manager.base_bridge import BridgeError
from AgentFrameWork.router import resolve as router_resolve, routes_for as router_routes_for
from AgentFrameWork.fsm_interpreter import AgentAbort
from AgentFrameWork.stream_bus import stream_bus
from services.agent_manager.service import AgentManager, Agent


def _get_agent_db() -> AgentsDB:
    return AgentsDB()


def _resolve_agent_ref(db: AgentsDB, agent_ref) -> Optional[Dict[str, Any]]:
    """agent_ref = int(agent_id) ou str(name). Retourne la ligne agents ou None."""
    if isinstance(agent_ref, int):
        return db.conn.execute("SELECT * FROM agents WHERE agent_id = ?",
                               (agent_ref,)).fetchone()
    if isinstance(agent_ref, str):
        return db.conn.execute("SELECT * FROM agents WHERE name = ?",
                               (agent_ref,)).fetchone()
    return None


def _resolve_agent_id(db: AgentsDB, agent_ref) -> Optional[int]:
    row = _resolve_agent_ref(db, agent_ref)
    return row["agent_id"] if row else None


class AgentDaemonError(Exception):
    """Erreur métier renvoyée par AgentDaemon.call()."""
    def __init__(self, message: str, code: int = 400):
        self.code = code
        super().__init__(message)


class AgentDaemon:
    """Point d'entrée unique pour toute opération sur un agent.

    call() resolve l'agent par nom ou id, valide l'opération via le routeur
    (rôle + état), exécute via le framework, retourne le résultat.
    """

    @staticmethod
    def call(agent_ref, function: str, **kwargs) -> Dict[str, Any]:
        """Execute `function` sur l'agent désigné par `agent_ref`.

        Args:
            agent_ref: int(agent_id) ou str(name).
            function: nom de l'opération (chat, research, pause, status, …).
            **kwargs: paramètres de l'opération.

        Returns:
            dict avec au minimum {"status": "ok"|"error", ...}
        """
        db = _get_agent_db()

        # ── Opérations spéciales (pas de résolution d'agent) ──
        if function in ("spawn",):
            return AgentDaemon._spawn(db, kwargs)
        if function in ("handoff",):
            return AgentDaemon._handoff(db, kwargs)
        if function in ("manager_status",):
            return AgentDaemon._manager_status(db)

        # ── Résoudre l'agent ──
        row = _resolve_agent_ref(db, agent_ref)
        if not row:
            msg = f"Agent {agent_ref} introuvable"
            if isinstance(agent_ref, str):
                return {"status": "error", "error": msg}
            return {"status": "error", "error": msg}
        agent = dict(row)
        agent_id = agent["agent_id"]

        # ── Opérations sans routage ──
        if function in ("evaluate",):
            return AgentDaemon._evaluate(db, agent, kwargs)
        if function in ("admit",):
            return AgentDaemon._admit(db, agent_id)
        if function in ("signals",):
            return AgentDaemon._signals(db, agent_id, kwargs)
        if function in ("signal/ack",):
            return AgentDaemon._signal_ack(kwargs)
        if function in ("signal/complete",):
            return AgentDaemon._signal_complete(kwargs)
        if function in ("stream",):
            return AgentDaemon._stream(agent_id, kwargs)
        if function in ("signal",):
            return AgentDaemon._signal(db, agent_id, kwargs)
        # "execute" = legacy direct execution (Phase 1/2)
        if function in ("execute",):
            return AgentDaemon._execute_op(db, agent, kwargs)

        # ── Résoudre la fonction via le routeur (rôle + état) ──
        route, reason = router_resolve(agent, function)
        if route is None:
            code = {"unknown": 404, "not_capable": 403, "state": 409}.get(reason, 404)
            return {"status": "error", "error": f"op_not_allowed: {reason}",
                    "reason": reason, "code": code}

        try:
            if route.kind == "lifecycle":
                return AgentDaemon._lifecycle_op(db, agent_id, function, kwargs)
            # capability : exécution via Agent.execute / chat_turn
            return AgentDaemon._capability_op(db, agent, function, kwargs)
        except AgentDaemonError as e:
            return {"status": "error", "error": str(e), "code": e.code}
        except Exception as e:
            import traceback
            return {"status": "error", "error": str(e), "trace": traceback.format_exc(), "code": 500}

    # ── Opérations spéciales (sans agent cible) ──

    @staticmethod
    def _manager_status(db: AgentsDB) -> Dict[str, Any]:
        mgr = AgentManager(db=db)
        active = mgr.list_active()
        zombies = mgr.check_heartbeats()
        return {
            "status": "ok",
            "active_agents": len(active),
            "active": [{"id": a["agent_id"], "name": a["name"]} for a in active],
            "zombies": zombies,
        }

    @staticmethod
    def _spawn(db: AgentsDB, kwargs: dict) -> Dict[str, Any]:
        name = kwargs.get("name")
        role = kwargs.get("role")
        request = kwargs.get("request", "")
        if not name or not role:
            return {"status": "error", "error": "name et role requis"}
        return AgentManager(db=db).spawn_agent(
            name=name, role=role, request=request,
            occupation=kwargs.get("occupation", "disparate"),
            resources=kwargs.get("resources"),
            config=kwargs.get("config"),
            provider_ref=kwargs.get("provider_ref", ""),
            model_ref=kwargs.get("model_ref", ""),
            keep_sleeping=kwargs.get("keep_sleeping", True),
        )

    @staticmethod
    def _handoff(db: AgentsDB, kwargs: dict) -> Dict[str, Any]:
        resolved = _handoff_resolve_ids(db, kwargs)
        from_id = resolved.get("from_id")
        to_id = resolved.get("to_id")
        if not from_id or not to_id:
            return {"status": "error", "error": "agent source ou cible introuvable"}
        return AgentManager(db=db).handoff(from_id, to_id)

    # ── Opérations sur agent (sans routage) ──

    @staticmethod
    def _evaluate(db: AgentsDB, agent: dict, kwargs: dict) -> Dict[str, Any]:
        from services.ressource_manager.service import RessourceManager
        try:
            resources = json.loads(agent.get("resources_json") or "{}")
        except (json.JSONDecodeError, TypeError):
            resources = {}
        resources.update(kwargs.get("resources", {}) or {})
        verdict = RessourceManager().evaluate(resources)
        verdict["agent_id"] = agent["agent_id"]
        verdict["status"] = "ok"
        return verdict

    @staticmethod
    def _admit(db: AgentsDB, agent_id: int) -> Dict[str, Any]:
        result = AgentManager(db=db).admit(agent_id)
        result["status"] = "ok"
        return result

    @staticmethod
    def _signal(db: AgentsDB, agent_id: int, kwargs: dict) -> Dict[str, Any]:
        stype = kwargs.get("type")
        if not stype:
            return {"status": "error", "error": "type requis"}
        return AgentManager(db=db).send_signal(agent_id, stype, kwargs.get("payload"))

    @staticmethod
    def _signals(db: AgentsDB, agent_id: int, kwargs: dict) -> Dict[str, Any]:
        status = kwargs.get("status")
        if status:
            rows = db.conn.execute(
                "SELECT * FROM agent_signals WHERE agent_id = ? AND status = ? ORDER BY signal_id",
                (agent_id, status)).fetchall()
        else:
            rows = db.conn.execute(
                "SELECT * FROM agent_signals WHERE agent_id = ? ORDER BY signal_id",
                (agent_id,)).fetchall()
        return {"status": "ok", "agent_id": agent_id,
                "signals": [dict(r) for r in rows], "count": len(rows)}

    @staticmethod
    def _signal_ack(kwargs: dict) -> Dict[str, Any]:
        sid = kwargs.get("signal_id")
        if not sid:
            return {"status": "error", "error": "signal_id requis"}
        return AgentManager().ack_signal(sid)

    @staticmethod
    def _signal_complete(kwargs: dict) -> Dict[str, Any]:
        sid = kwargs.get("signal_id")
        if not sid:
            return {"status": "error", "error": "signal_id requis"}
        return AgentManager().complete_signal(sid, kwargs.get("result"))

    @staticmethod
    def _stream(agent_id: int, kwargs: dict) -> Dict[str, Any]:
        seq = kwargs.get("seq", 0) or 0
        chunks = stream_bus.since(agent_id, seq)
        last = chunks[-1]["seq"] if chunks else seq
        return {"status": "ok", "agent_id": agent_id, "seq": last,
                "chunks": chunks, "count": len(chunks)}

    # ── Opérations routées ──

    @staticmethod
    def _lifecycle_op(db: AgentsDB, agent_id: int, function: str, kwargs: dict) -> Dict[str, Any]:
        """Lifecycle : status / configure / pause / resume / kill."""
        mgr = AgentManager(db=db)
        if function == "status":
            row = mgr.get_by_id(agent_id)
            return {"status": "ok", "agent": dict(row)} if row else {"status": "error", "error": "introuvable"}
        if function == "configure":
            row = mgr.get_by_id(agent_id)
            if not row:
                return {"status": "error", "error": "introuvable"}
            cfg = json.loads(row.get("config_json") or "{}")
            if "temperature" in kwargs:
                cfg["temperature"] = float(kwargs["temperature"])
            if "max_tokens" in kwargs:
                cfg["max_tokens"] = int(kwargs["max_tokens"])
            db.conn.execute(
                "UPDATE agents SET config_json = ? WHERE agent_id = ?",
                (json.dumps(cfg), agent_id))
            db.conn.commit()
            return {"status": "ok", "config": cfg}
        # pause / resume / kill → signal canal de supervision
        sig = mgr.send_signal(agent_id, function)
        return {"status": "ok", "signal": sig}

    @staticmethod
    def _capability_op(db: AgentsDB, agent: dict, function: str, kwargs: dict) -> Dict[str, Any]:
        """Capability : chat / research / summarize / … → exécution FSM."""
        agent_id = agent["agent_id"]

        # Résoudre provider/model (Phase 3 : Organisateur si non imposé)
        provider_ref = kwargs.get("provider_ref") or kwargs.get("provider") or ""
        model_ref = kwargs.get("model_ref") or kwargs.get("model") or ""
        if not provider_ref or not model_ref:
            try:
                resources = json.loads(agent.get("resources_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                resources = {}
            if resources.get("llm"):
                from modules.llm_manager.organisateur import Organisateur
                alloc = Organisateur().allocate(resources)
                if alloc["allocated"]:
                    if not provider_ref:
                        provider_ref = alloc["provider_ref"]
                    if not model_ref:
                        model_ref = alloc["model_ref"]
                else:
                    return {"status": "error", "error": f"LLM non allouable : {alloc['reason']}"}

        a = Agent.hydrate(agent_id, db)
        try:
            msg = kwargs.get("message") or kwargs.get("request") or ""
            if function == "chat":
                res = a.chat_turn(
                    msg,
                    provider_ref=provider_ref,
                    model_ref=model_ref,
                    temperature=float(kwargs.get("temperature", 0.7)),
                    max_tokens=int(kwargs.get("max_tokens", 4096)),
                    system_prompt=kwargs.get("system_prompt", ""),
                )
            else:
                res = a.execute(
                    msg,
                    provider_ref=provider_ref,
                    model_ref=model_ref,
                    temperature=float(kwargs.get("temperature", 0.7)),
                    max_tokens=int(kwargs.get("max_tokens", 4096)),
                    entrypoint=kwargs.get("entrypoint", "main"),
                )
        finally:
            a.dehydrate()
        return res

    # ── Legacy : execute direct (Phase 1/2, via agent/execute) ──

    @staticmethod
    def _execute_op(db: AgentsDB, agent: dict, kwargs: dict) -> Dict[str, Any]:
        """Exécution directe d'un agent (héritée de agent/execute).

        Résout le provider/LLM (Phase 3 : Organisateur si non imposé),
        hydrate l'agent, exécute, déshydrate.
        """
        agent_id = agent["agent_id"]
        request = kwargs.get("request", "")
        if not request:
            return {"status": "error", "error": "request requis"}

        provider_ref = kwargs.get("provider_ref", "")
        model_ref = kwargs.get("model_ref", "")
        if not provider_ref or not model_ref:
            try:
                resources = json.loads(agent.get("resources_json") or "{}")
            except (json.JSONDecodeError, TypeError):
                resources = {}
            if resources.get("llm"):
                from modules.llm_manager.organisateur import Organisateur
                alloc = Organisateur().allocate(resources)
                if alloc["allocated"]:
                    if not provider_ref:
                        provider_ref = alloc["provider_ref"]
                    if not model_ref:
                        model_ref = alloc["model_ref"]
                else:
                    return {"status": "error", "error": f"LLM non allouable : {alloc['reason']}"}

        try:
            a = Agent.hydrate(agent_id, db)
            result = a.execute(
                request,
                provider_ref=provider_ref,
                model_ref=model_ref,
                temperature=float(kwargs.get("temperature", 0.7)),
                max_tokens=int(kwargs.get("max_tokens", 4096)),
                entrypoint=kwargs.get("entrypoint", "main"),
            )
            a.dehydrate()
            return result
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ── Gestion d'état (auto-régénération au démarrage) ──

    @staticmethod
    def sync() -> Dict[str, Any]:
        """Scanne la table agents, nettoie les orphelins runtime.

        Appelé au démarrage de l'AFD (ou du daemon) pour se synchroniser
        avec l'état BDD.
        """
        db = _get_agent_db()
        mgr = AgentManager(db=db)
        zombies = mgr.check_heartbeats(max_age_seconds=0)
        cleaned = 0
        for aid in zombies:
            db.conn.execute(
                "UPDATE agents SET status = 'STOPPED' WHERE agent_id = ?", (aid,))
            db.conn.execute(
                "DELETE FROM agent_runtime WHERE agent_id = ?", (aid,))
            cleaned += 1
        db.conn.commit()
        # Lister les agents non-sleeping (à reprendre)
        rows = db.conn.execute(
            "SELECT agent_id, name, status FROM agents WHERE status NOT IN ('INIT', 'STOPPED', 'SLEEPING')"
        ).fetchall()
        return {
            "zombies_cleaned": cleaned,
            "agents_active_on_disk": [dict(r) for r in rows],
        }

    # ── Découverte des capacités ──

    @staticmethod
    def capabilities_catalog() -> Dict[str, object]:
        from AgentFrameWork.router import capabilities_catalog
        return capabilities_catalog()

    @staticmethod
    def routes_for(agent_ref) -> List[Dict[str, str]]:
        db = _get_agent_db()
        row = _resolve_agent_ref(db, agent_ref)
        if not row:
            return []
        agent = dict(row)
        routes = router_routes_for(
            agent.get("role_type", ""),
            agent.get("status", "INIT"),
        )
        return [r.to_dict() for r in routes]


def _handoff_resolve_ids(db: AgentsDB, kwargs: dict) -> dict:
    """Résout les noms en ids pour handoff."""
    out = {}
    from_name = kwargs.get("from_name")
    to_name = kwargs.get("to_name")
    if from_name:
        row = db.conn.execute("SELECT agent_id FROM agents WHERE name = ?",
                              (from_name,)).fetchone()
        out["from_id"] = row["agent_id"] if row else None
    else:
        out["from_id"] = kwargs.get("from_id")
    if to_name:
        row = db.conn.execute("SELECT agent_id FROM agents WHERE name = ?",
                              (to_name,)).fetchone()
        out["to_id"] = row["agent_id"] if row else None
    else:
        out["to_id"] = kwargs.get("to_id")
    return out
