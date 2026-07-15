"""WatcherService — Gestion des agents surveillants.

Le WatcherService vérifie périodiquement si les conditions de surveillance
des agents Watchers sont remplies et les réveille si c'est le cas.
"""

import logging
from typing import Any, Dict, List
from sql.db import ModelWeaverDB

logger = logging.getLogger("modelweaver.watcher")


class WatcherService:
    """Service qui gère le réveil des agents based on events."""

    def __init__(self, db: ModelWeaverDB, dispatcher: Any):
        self.db = db
        self.dispatcher = dispatcher

    def tick(self) -> int:
        """Vérifie les watchers dont le délai est écoulé et les déclenche.
        
        Retourne le nombre d'agents réveillés.
        """
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. Liste les watchers mûrs pour un check
        due_watchers = self.db.watchers.list_due(now_iso)
        if not due_watchers:
            return 0

        triggered_count = 0
        for watcher in due_watchers:
            # 2. Vérifie si la condition de surveillance est remplie
            if self._check_condition(watcher):
                # 3. Réveille l'agent associé
                if self._trigger_agent(watcher):
                    triggered_count += 1
            
            # 4. Marque le watcher comme vérifié
            self.db.watchers.mark_checked(watcher["watcher_id"])
        
        self.db.commit()
        return triggered_count

    def _check_condition(self, watcher: Dict[str, Any]) -> bool:
        """Vérifie si l'événement surveillé a eu lieu."""
        w_type = watcher["watch_type"]
        criteria = watcher.get("filter_criteria") # JSON string ou dict
        
        if w_type == "tasks":
            # Surveille l'apparition de nouvelles tâches TODO correspondant au rôle
            # On utilise le dispatcher pour voir s'il y a des tâches en attente
            role = watcher["role_type"] # On récupère le rôle de l'agent watcher
            count = self.db.shared_tasks.count_pending(role=role)
            return count > 0
            
        elif w_type == "queue":
            # Surveille les nouveaux messages dans la queue pour l'agent
            count = self.db.queue.count_unread(watcher["agent_id"])
            return count > 0
            
        elif w_type == "successor_requests":
            # Surveille les demandes de succession (broadcast)
            cur = self.db.conn.execute(
                "SELECT count(*) as cnt FROM agent_queue WHERE topic='succession_request' AND status='TODO'"
            )
            return cur.fetchone()["cnt"] > 0
            
        return False

    def _trigger_agent(self, watcher: Dict[str, Any]) -> bool:
        """Déclenche l'agent associé au watcher."""
        agent_id = watcher["agent_id"]
        
        # On crée une wakeup_call pour l'agent avec le skill 'watcher_event'
        # On récupère une session active ou on en crée une
        sessions = self.db.sessions.list_all(agent_id=agent_id, status="ACTIVE")
        session_id = sessions[0]["session_id"] if sessions else self.db.sessions.create(agent_id)
        
        self.db.wakeup_calls.create(
            agent_id=agent_id,
            session_id=session_id,
            skill="watcher_event",
            request_payload=json.dumps({"watch_type": watcher["watch_type"], "watcher_id": watcher["watcher_id"]}),
            execute_after=None # Immédiat
        )
        return True

import json
