"""Dispatcher — Orchestrateur technique du flux de travail.

Le Dispatcher fait le pont entre les tâches partagées (shared_tasks)
et les agents disponibles. Il s'assure que chaque tâche est assignée
à l'agent le plus approprié.
"""

import logging
from typing import Any, Dict, List, Optional
from sql.db import ModelWeaverDB
from AgentFrameWork.provisioning import ProvisioningService

logger = logging.getLogger("modelweaver.dispatcher")


class Dispatcher:
    """Contrôleur déterministe pour l'assignation des tâches."""

    def __init__(self, db: ModelWeaverDB, provisioning_service: ProvisioningService):
        self.db = db
        self.provisioning = provisioning_service

    def dispatch_pending_tasks(self) -> int:
        """Tente d'assigner les tâches en attente à des agents.
        
        Retourne le nombre de tâches assignées.
        """
        tasks = self.db.shared_tasks.list_pending(limit=10)
        if not tasks:
            return 0

        assigned_count = 0
        for task in tasks:
            task_id = task["task_id"]
            req_role = task["required_role"]
            context = task["context"]

            # 1. Chercher un agent IDLE compatible
            agent = self._find_compatible_agent(req_role, context)

            if agent:
                # Agent trouvé ! On l'assigne
                if self.db.shared_tasks.claim(task_id, agent["agent_id"]):
                    self._create_wakeup_call(agent["agent_id"], task)
                    assigned_count += 1
            else:
                # 2. Aucun agent disponible -> On en provisionne un nouveau
                # IMPORTANT: On marque la tâche comme IN_PROGRESS tout de suite
                # pour éviter que le prochain cycle du Ticker ne relance un provisionnement.
                logger.info("Dispatcher: Aucun agent %s disponible pour '%s'. Provisionnement...", req_role, task['title'])
                
                # On "claim" la tâche sans assigné pour bloquer le dispatcher
                self.db.conn.execute(
                    "UPDATE shared_tasks SET status='IN_PROGRESS' WHERE task_id=?", 
                    (task_id,)
                )
                
                new_agent_id = self.provisioning.request_agent(req_role, context)
                if new_agent_id:
                    # On assigne l'agent nouvellement créé
                    self.db.conn.execute(
                        "UPDATE shared_tasks SET assigned_to=? WHERE task_id=?", 
                        (new_agent_id, task_id)
                    )
                    self._create_wakeup_call(new_agent_id, task)
                    assigned_count += 1
                else:
                    # Échec du provisionnement -> on remet en TODO
                    self.db.conn.execute(
                        "UPDATE shared_tasks SET status='TODO' WHERE task_id=?", 
                        (task_id,)
                    )

        self.db.commit()
        return assigned_count

    def _find_compatible_agent(self, role_type: Optional[str], context: Optional[str]) -> Optional[Dict]:
        """Cherche un agent IDLE qui correspond au rôle et au contexte."""
        # On cherche les agents IDLE
        idle_agents = self.db.agents.list_all(status="IDLE")
        
        for agent in idle_agents:
            # Vérification du rôle
            if role_type and agent["role_type"] != role_type:
                continue
            
            # Vérification du contexte (si défini dans la config de l'agent)
            if context:
                config = json.loads(agent["config_json"]) if agent.get("config_json") else {}
                agent_contexts = config.get("contexts", [])
                if context not in agent_contexts and "general" not in agent_contexts:
                    continue
            
            return agent
        return None

    def _create_wakeup_call(self, agent_id: int, task: Dict) -> None:
        """Crée la wakeup_call pour déclencher l'exécution de la tâche."""
        payload = json.dumps({
            "task_id": task["task_id"],
            "title": task["title"],
            "description": task["description"],
            "context": task["context"]
        })
        
        self.db.wakeup_calls.create(
            agent_id=agent_id,
            session_id=self._get_or_create_session(agent_id),
            skill="shared_task",
            request_payload=payload,
        )

    def _get_or_create_session(self, agent_id: int) -> str:
        sessions = self.db.sessions.list_all(agent_id=agent_id, status="ACTIVE")
        if sessions:
            return sessions[0]["session_id"]
        return self.db.sessions.create(agent_id)

import json