"""Scheduler — Gestionnaire de planification et de récurrence.

Le Scheduler surveille les jobs planifiés et crée des wakeup_calls 
pour les déclencher au moment opportun.
"""

import logging
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sql.db import ModelWeaverDB

logger = logging.getLogger("modelweaver.scheduler")


class Scheduler:
    """Service de planification des tâches d'agents."""

    def __init__(self, db: ModelWeaverDB, dispatcher: Any):
        self.db = db
        self.dispatcher = dispatcher

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def tick(self) -> int:
        """Vérifie les jobs dus et les déclenche.
        
        Retourne le nombre de jobs déclenchés.
        """
        jobs = self.db.scheduled_jobs.list_due()
        if not jobs:
            return 0

        triggered_count = 0
        for job in jobs:
            if self._trigger_job(job):
                triggered_count += 1
        
        self.db.commit()
        return triggered_count

    def _trigger_job(self, job: Dict[str, Any]) -> bool:
        """Déclenche un job et calcule sa prochaine exécution."""
        job_id = job["job_id"]
        skill = job["skill"]
        payload = job["request_payload"]
        agent_id = job["agent_id"]
        role_type = job["role_type"]

        # 1. Résolution de l'agent
        target_agent_id = None
        if agent_id:
            target_agent_id = agent_id
        elif role_type:
            # On utilise le dispatcher pour trouver un agent compatible ou en provisionner un
            # On crée une "fake" shared_task temporaire pour utiliser la logique du dispatcher
            # ou on implémente une version simplifiée ici.
            # Pour éviter les cycles, on va appeler une méthode helper du dispatcher.
            target_agent_id = self.dispatcher._find_compatible_agent(role_type, None)
            if target_agent_id:
                target_agent_id = target_agent_id["agent_id"]
            else:
                # Provisionnement via le dispatcher
                target_agent_id = self.dispatcher.provisioning.request_agent(role_type, None)

        if not target_agent_id:
            logger.error("Scheduler: Impossible de trouver un agent pour le job %d (%s)", job_id, role_type)
            return False

        # 2. Création de la wakeup_call
        # On récupère ou crée une session pour l'agent
        sessions = self.db.sessions.list_all(agent_id=target_agent_id, status="ACTIVE")
        session_id = sessions[0]["session_id"] if sessions else self.db.sessions.create(target_agent_id)

        self.db.wakeup_calls.create(
            agent_id=target_agent_id,
            session_id=session_id,
            skill=skill,
            request_payload=payload,
            execute_after=self._now_iso()
        )

        # 3. Calcul du prochain run
        interval = job["interval_seconds"]
        if interval and interval > 0:
            next_run = (datetime.now(timezone.utc) + timedelta(seconds=interval)).strftime("%Y-%m-%d %H:%M:%S")
            self.db.scheduled_jobs.update_next_run(job_id, next_run)
        else:
            # One-shot: on désactive le job
            self.db.conn.execute("UPDATE scheduled_jobs SET enabled = 0 WHERE job_id = ?", (job_id,))

        return True

    def schedule_task(self, skill: str, payload: Optional[str] = None, 
                      run_at: Optional[str] = None, interval: int = 0, 
                      agent_id: Optional[int] = None, role_type: Optional[str] = None) -> int:
        """Planifie une nouvelle tâche."""
        data = {
            "agent_id": agent_id,
            "role_type": role_type,
            "skill": skill,
            "request_payload": payload,
            "interval_seconds": interval,
            "next_run_at": run_at or self._now_iso(),
            "enabled": 1
        }
        return self.db.scheduled_jobs.save(data)
