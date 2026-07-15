"""WatchdogService — Surveillance de la santé des agents.

Le Watchdog détecte les agents qui sont restés dans l'état 'BUSY' 
pendant une période anormalement longue (zombies) et les réinitialise.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List
from sql.db import ModelWeaverDB

logger = logging.getLogger("modelweaver.watchdog")


class WatchdogService:
    """Surveille et nettoie les agents bloqués."""

    def __init__(self, db: ModelWeaverDB, timeout_seconds: int = 1800):
        self.db = db
        self.timeout_seconds = timeout_seconds

    def check_zombies(self) -> int:
        """Identifie et réinitialise les agents BUSY depuis trop longtemps.
        
        Retourne le nombre d'agents reset.
        """
        # On récupère les agents BUSY
        busy_agents = self.db.agents.list_all(status="BUSY")
        if not busy_agents:
            return 0

        reset_count = 0
        now = datetime.now(timezone.utc)

        for agent in busy_agents:
            # On vérifie la date de dernière mise à jour (création si NULL)
            # Note: On utilise updated_at de la table agents ou on check la wakeup_call active
            if self._is_zombie(agent, now):
                logger.warning("Watchdog: Agent %d (%s) détecté comme zombie. Reset -> IDLE", 
                               agent["agent_id"], agent["name"])
                
                # 1. Reset l'agent
                self.db.agents.update_status(agent["agent_id"], "IDLE")
                
                # 2. On marque la wakeup_call correspondante comme FAILED pour éviter le blocage
                # On cherche la tâche BUSY liée à cet agent
                cur = self.db.conn.execute(
                    "SELECT task_id FROM wakeup_calls WHERE agent_id = ? AND status = 'BUSY'",
                    (agent["agent_id"],)
                )
                task = cur.fetchone()
                if task:
                    self.db.wakeup_calls.fail(task["task_id"], "Watchdog: timeout d'exécution")
                
                reset_count += 1

        self.db.commit()
        return reset_count

    def _is_zombie(self, agent: Dict, now: datetime) -> bool:
        """Vérifie si l'agent a dépassé le timeout."""
        # On utilise la date de création de l'agent ou la dernière session comme fallback
        # car la table 'agents' n'a pas de 'updated_at' global.
        # Le plus fiable est de regarder la wakeup_call active.
        cur = self.db.conn.execute(
            "SELECT created_at FROM wakeup_calls WHERE agent_id = ? AND status = 'BUSY' LIMIT 1",
            (agent["agent_id"],)
        )
        row = cur.fetchone()
        if not row:
            return False # Pas de tâche active, donc pas zombie (ou status incohérent)

        try:
            created_at = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            diff = (now - created_at).total_seconds()
            return diff > self.timeout_seconds
        except Exception:
            return False
