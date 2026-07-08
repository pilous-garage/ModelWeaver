"""Agent Factory — Création, exécution et cycle de vie des agents.

createAgent crée un agent persistant en BDD avec son provider et son rôle.
L'objet Agent retourné permet d'interagir avec lui : execute(), exit().
"""

import json
from typing import Any, Dict, List, Optional

from sql.db import ModelWeaverDB
from agents.role_manager import RoleManager
from agents.worker import Worker


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

    def execute(
        self,
        request: str,
        additional_context: Optional[str] = None,
        reset_context: bool = False,
        session_id: Optional[str] = None,
        skill: str = "chat",
    ) -> Dict[str, Any]:
        """Crée une wakeup_call pour cet agent et l'exécute immédiatement.

        Args:
            request: La requête à envoyer à l'agent.
            additional_context: Contexte supplémentaire à injecter.
            reset_context: Si True, archive la session actuelle et en crée une nouvelle.
            session_id: Session existante (si None, utilise la session active).
            skill: Le skill à exécuter (défini par le rôle).

        Returns:
            Le résultat de l'exécution.
        """
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

    def exit(self) -> bool:
        """Arrête l'agent et le marque comme STOPPED."""
        self.db.agents.update_status(self.agent_id, "STOPPED")
        active_sessions = self.db.sessions.list_all(agent_id=self.agent_id, status="ACTIVE")
        for sess in active_sessions:
            self.db.sessions.update_status(sess["session_id"], "ARCHIVED")
        self.db.commit()
        self.status = "STOPPED"
        return True

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
    ) -> Agent:
        """Crée un agent persistant.

        Args:
            name: Nom unique de l'agent.
            role_type: Type de rôle (pointe vers un fichier YAML dans roles/).
            provider_id: ID du model_provider à utiliser.
            config: Configuration supplémentaire (écrase/surcharge le rôle).

        Returns:
            L'objet Agent permettant d'interagir.
        """
        role_def = self.roles.get_role(role_type)
        merged_config = dict(role_def.default_config if role_def else {})
        if config:
            merged_config.update(config)
        if role_def:
            merged_config["system_prompt"] = role_def.system_prompt
            merged_config["allowed_skills"] = role_def.allowed_skills

        agent_id = self.db.agents.save({
            "name": name,
            "role_type": role_type,
            "provider_id": provider_id,
            "status": "IDLE",
            "config": merged_config,
        })

        self.db.sessions.create(agent_id, context_summary=f"Agent {name} créé")
        self.db.commit()

        agent_data = self.db.agents.get(agent_id)
        return Agent(self.db, agent_data, self._worker)

    def create_request_agent(
        self,
        name: str,
        role_type: str,
        request: str,
        provider_id: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Crée un agent jetable, exécute une requête, et l'arrête.

        Retourne le résultat directement sans garder l'agent actif.
        """
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
        return Agent(self.db, data, self._worker)

    def list_agents(self, status: Optional[str] = None,
                    role_type: Optional[str] = None) -> List[Agent]:
        rows = self.db.agents.list_all(status=status, role_type=role_type)
        return [Agent(self.db, r, self._worker) for r in rows]

    def delete_agent(self, agent_id: int) -> bool:
        return self.db.agents.delete(agent_id)
