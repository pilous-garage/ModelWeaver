"""Agent Factory — Création, exécution et cycle de vie des agents.

createAgent crée un agent persistant en BDD avec son provider et son rôle.
L'objet Agent retourné permet d'interagir avec lui : execute(), exit(), connect().

Supporte le save/restore state (state_json) et la succession (exit avec successeur).
"""

import json
from typing import Any, Dict, List, Optional

from sql.db import ModelWeaverDB
from AgentsCatalogue.role_manager import RoleManager
from AgentFrameWork.worker import Worker


class Agent:
    """Représentation runtime d'un agent.

    L'agent lui-même est une ligne en BDD. Cette classe est une
    enveloppe pratique pour interagir avec lui.
    """

    def __init__(self, db: ModelWeaverDB, agent_data: Dict[str, Any], worker: Worker):
        self.db = db
        self._data = agent_data
        self._worker = worker
        self.agent_id = agent_data["agent_id"]
        self.name = agent_data["name"]
        self.role_type = agent_data["role_type"]
        self.status = agent_data["status"]
        self._state: Dict[str, Any] = {}

    # ── State management ──────────────────────────────────

    def save_state(self, state: Optional[Dict[str, Any]] = None) -> None:
        """Sauvegarde l'état de l'agent en BDD."""
        if state is None:
            state = self._state
        self.db.agents.save_state(self.agent_id, state)
        self.db.commit()

    def restore_state(self) -> Dict[str, Any]:
        """Charge l'état sauvegardé depuis la BDD."""
        self._state = self.db.agents.load_state(self.agent_id)
        return self._state

    def set_state(self, key: str, value: Any) -> None:
        self._state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    # ── Connexions (branches) ─────────────────────────────

    def connect(self, channel: str, target: Optional[str] = None,
                config: Optional[Dict] = None) -> int:
        """Connecte l'agent à un canal (chatroom, todo, queue, file, api, agent).

        Args:
            channel: Type de canal.
            target: Nom de l'agent cible (si channel='agent'), chemin/URL sinon.
            config: Configuration supplémentaire.
        """
        target_id = None
        if channel == "agent" and target:
            target_agent = self.db.agents.get_by_name(target)
            if target_agent:
                target_id = target_agent["agent_id"]

        conn_id = self.db.connections.connect(
            agent_id=self.agent_id, channel=channel,
            target_id=target_id, config=config
        )
        self.db.commit()
        return conn_id

    def disconnect(self, channel: str, target: Optional[str] = None) -> bool:
        """Déconnecte l'agent d'un canal."""
        target_id = None
        if channel == "agent" and target:
            target_agent = self.db.agents.get_by_name(target)
            if target_agent:
                target_id = target_agent["agent_id"]

        result = self.db.connections.disconnect(self.agent_id, channel, target_id)
        self.db.commit()
        return result

    def list_connections(self) -> List[Dict[str, Any]]:
        """Liste les connexions actives de l'agent."""
        return self.db.connections.list_by_agent(self.agent_id, enabled_only=True)

    # ── Exécution ──────────────────────────────────────────

    def execute(
        self,
        request: str,
        additional_context: Optional[str] = None,
        reset_context: bool = False,
        session_id: Optional[str] = None,
        skill: str = "chat",
    ) -> Dict[str, Any]:
        """Crée une wakeup_call pour cet agent et l'exécute immédiatement."""
        if self.status != "IDLE":
            return {"status": "error", "message": f"Agent status is {self.status}, not IDLE"}

        if reset_context:
            if session_id:
                self.db.sessions.update_status(session_id, "ARCHIVED")
            session_id = None

        if not session_id:
            sessions = self.db.sessions.list_all(agent_id=self.agent_id, status="ACTIVE")
            if sessions:
                session_id = sessions[0]["session_id"]
            else:
                session_id = self.db.sessions.create(self.agent_id)

        if additional_context:
            self.db.agent_messages.add(
                session_id=session_id,
                role="system",
                content=f"[Contexte supplémentaire]\n{additional_context}",
            )

        self.db.agent_messages.add(
            session_id=session_id,
            role="user",
            content=request,
        )

        payload = json.dumps({"request": request, "additional_context": additional_context})
        task_id = self.db.wakeup_calls.create(
            agent_id=self.agent_id,
            session_id=session_id,
            skill=skill,
            request_payload=payload,
        )

        self.db.commit()
        result = self._worker.execute(task_id)
        return result

    # ── Fin de vie ─────────────────────────────────────────

    def exit(self, successor_role: Optional[str] = None,
             successor_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Arrête l'agent. Si successor_role est fourni, crée un successeur
        qui hérite des sessions actives et marque l'agent TERMINATED.

        Args:
            successor_role: Rôle du successeur (ex: 'critique'). Si None,
                l'agent est juste STOPPED.
            successor_config: Config supplémentaire pour le successeur.

        Returns:
            Dictionnaire avec le statut et l'ID du successeur si applicable.
        """
        result = {"status": "stopped"}

        if successor_role:
            # Sauvegarder l'état avant de partir
            self.save_state()

            # Créer le successeur
            factory = AgentFactory(db=self.db)
            successor = factory.create_agent(
                name=f"{self.name}_successor",
                role_type=successor_role,
                provider_id=self._data.get("provider_id"),
                config=successor_config,
            )

            # Transférer les sessions actives au successeur
            self.db.sessions.transfer_to_agent(self.agent_id, successor.agent_id)

            # Marquer TERMINATED avec lien vers le successeur
            self.db.agents.terminate(self.agent_id, successor_id=successor.agent_id)
            self.db.commit()

            self.status = "TERMINATED"
            result = {
                "status": "terminated",
                "successor_id": successor.agent_id,
                "successor_name": successor.name,
            }
        else:
            self.db.agents.update_status(self.agent_id, "STOPPED")
            active_sessions = self.db.sessions.list_all(agent_id=self.agent_id, status="ACTIVE")
            for sess in active_sessions:
                self.db.sessions.update_status(sess["session_id"], "ARCHIVED")
            self.db.commit()
            self.status = "STOPPED"

        return result

    def signal_relay(self, reason: str,
                        successor_role: Optional[str] = None) -> int:
        """Envoie un signal de relais dans la queue (pour l'orchestrateur)."""
        content = json.dumps({
            "type": "succession_request",
            "agent_id": self.agent_id,
            "reason": reason,
            "successor_role": successor_role,
            "sessions": [s["session_id"] for s in
                        self.db.sessions.list_all(agent_id=self.agent_id, status="ACTIVE")],
        })

        msg_id = self.db.queue.send_broadcast(
            from_agent_id=self.agent_id,
            content=content,
            topic="succession_request",
            message_type="notification",
        )
        self.db.commit()
        return msg_id

    def send(self, target_name: str, message: str) -> int:
        """Envoie un message direct à un autre agent."""
        target = self.db.agents.get_by_name(target_name)
        if not target:
            raise ValueError(f"Agent {target_name} introuvable")
        
        msg_id = self.db.queue.send_direct(
            from_id=self.agent_id,
            to_id=target["agent_id"],
            content=message
        )
        self.db.commit()
        return msg_id

    def broadcast(self, message: str, topic: str = "general") -> int:
        """Diffuse un message dans le chatroom / broadcast."""
        msg_id = self.db.queue.send_broadcast(
            from_agent_id=self.agent_id,
            content=message,
            topic=topic
        )
        self.db.commit()
        return msg_id

    def add_task(self, title: str, role: Optional[str] = None, 
                 context: str = "general", description: Optional[str] = None) -> int:
        """Ajoute une tâche dans le todo partagé."""
        task_id = self.db.shared_tasks.create(
            title=title,
            description=description,
            required_role=role,
            context=context
        )
        self.db.commit()
        return task_id

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        sessions = self.db.sessions.list_all(agent_id=self.agent_id, status="ACTIVE")
        if not sessions:
            return []
        return self.db.agent_messages.list_by_session(sessions[0]["session_id"], limit=limit)

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)


class AgentFactory:
    """Factory pour créer et gérer des agents."""

    def __init__(self, db: Optional[ModelWeaverDB] = None, roles_dir: Optional[str] = None):
        self.db = db or ModelWeaverDB()
        self.roles = RoleManager(roles_dir)
        self._worker = Worker(
            agents=self.db.agents,
            model_providers=self.db.model_providers,
            sessions=self.db.sessions,
            messages=self.db.agent_messages,
            wakeup_calls=self.db.wakeup_calls,
            api_keys_repo=self.db.keys,
            db_conn=self.db.conn,
        )

    def create_agent(
        self,
        name: str,
        role_type: str,
        provider_id: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
        inherit_state_from: Optional[int] = None,
    ) -> Agent:
        """Crée un agent persistant.

        Args:
            name: Nom unique de l'agent.
            role_type: Type de rôle (pointe vers un fichier YAML dans roles/).
            provider_id: ID du model_provider à utiliser.
            config: Configuration supplémentaire (écrase/surcharge le rôle).
            inherit_state_from: ID d'un agent dont on hérite l'état (succession).

        Returns:
            L'objet Agent permettant d'interagir.
        """
        role_def = self.roles.get_role(role_type)
        merged_config = dict(role_def.default_config if role_def else {})
        if config:
            merged_config.update(config)
        if role_def:
            merged_config["system_prompt"] = role_def.system_prompt
            merged_config["skills"] = role_def.skills

        # Récupérer le workflow depuis le rôle si présent
        state = {}
        if inherit_state_from:
            state = self.db.agents.load_state(inherit_state_from)

        agent_id = self.db.agents.save({
            "name": name,
            "role_type": role_type,
            "provider_id": provider_id,
            "status": "IDLE",
            "config": merged_config,
            "state": state,
        })

        self.db.sessions.create(agent_id, context_summary=f"Agent {name} créé")

        # Branches automatiques selon la config du rôle
        if role_def:
            branches = role_def.raw.get("branches", {})
            if branches.get("chatroom"):
                self.db.connections.connect(agent_id, "chatroom")
            if branches.get("todo"):
                self.db.connections.connect(agent_id, "todo")
            if branches.get("queue", True):
                self.db.connections.connect(agent_id, "queue")

        self.db.commit()

        agent_data = self.db.agents.get(agent_id)
        agent = Agent(self.db, agent_data, self._worker)
        if state:
            agent._state = state
        return agent

    def create_request_agent(
        self,
        name: str,
        role_type: str,
        request: str,
        provider_id: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Crée un agent jetable, exécute une requête, et l'arrête."""
        agent = self.create_agent(name, role_type, provider_id, config)
        result = agent.execute(request)
        agent.exit()
        self.db.commit()
        return result

    def get_agent(self, agent_id: Optional[int] = None,
                  name: Optional[str] = None) -> Optional[Agent]:
        """Récupère un agent par ID ou nom."""
        data = self.db.agents.get(agent_id) if agent_id else self.db.agents.get_by_name(name)
        if not data:
            return None
        agent = Agent(self.db, data, self._worker)
        agent.restore_state()
        return agent

    def list_agents(self, status: Optional[str] = None,
                    role_type: Optional[str] = None) -> List[Agent]:
        rows = self.db.agents.list_all(status=status, role_type=role_type)
        agents = []
        for r in rows:
            agent = Agent(self.db, r, self._worker)
            agent.restore_state()
            agents.append(agent)
        return agents

    def delete_agent(self, agent_id: int) -> bool:
        return self.db.agents.delete(agent_id)
