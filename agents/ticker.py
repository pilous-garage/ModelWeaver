"""AsyncTicker — Boucle asynchrone qui surveille wakeup_calls.

Au repos : 0% CPU (asyncio.Event en attente).
À l'activation : réveil immédiat via un Event déclenché par les Writers.

Cycle :
  1. Anti-fantôme au démarrage (BUSY → TODO)
  2. Boucle : attend un event ou un intervalle
  3. Récupère les tâches mûres (execute_after <= now)
  4. Assigne chaque tâche via une transaction IMMEDIATE
  5. Lance un Worker pour chaque tâche
  6. Retourne en attente
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from sql.db import ModelWeaverDB
from agents.worker import Worker

logger = logging.getLogger("modelweaver.ticker")


class AsyncTicker:
    """Ticker asynchrone avec wake-up immédiat.

    Usage:
        ticker = AsyncTicker()
        await ticker.start()  # Boucle forever
    """

    def __init__(
        self,
        db: Optional[ModelWeaverDB] = None,
        poll_interval: float = 1.0,
        max_tasks_per_cycle: int = 5,
    ):
        self.db = db or ModelWeaverDB()
        self.poll_interval = poll_interval
        self.max_tasks_per_cycle = max_tasks_per_cycle
        self._wake_event = asyncio.Event()
        self._running = False

        self._worker = Worker(
            agents=self.db.agents,
            model_providers=self.db.model_providers,
            sessions=self.db.sessions,
            messages=self.db.agent_messages,
            wakeup_calls=self.db.wakeup_calls,
            api_keys_repo=self.db.keys,
            db_conn=self.db.conn,
        )

    def wakeup(self):
        """Déclenche un réveil immédiat du Ticker (thread-safe)."""
        self._wake_event.set()

    async def start(self):
        """Boucle principale du Ticker — tourne jusqu'à stop()."""
        self._running = True

        ghost_count = self.db.wakeup_calls.reset_busy()
        if ghost_count > 0:
            logger.warning(f"Anti-fantôme : {ghost_count} tâches BUSY → TODO")
        self.db.commit()

        logger.info("Ticker démarré (poll=%ss, max=%d par cycle)",
                     self.poll_interval, self.max_tasks_per_cycle)

        while self._running:
            processed = self._process_cycle()

            if processed > 0:
                logger.debug("Ticker : %d tâches traitées", processed)

            if processed < self.max_tasks_per_cycle:
                self._wake_event.clear()
                try:
                    await asyncio.wait_for(
                        self._wake_event.wait(),
                        timeout=self.poll_interval,
                    )
                except asyncio.TimeoutError:
                    pass

        logger.info("Ticker arrêté")

    async def stop(self):
        """Arrête le Ticker proprement."""
        self._running = False
        self.wakeup()

    def _process_cycle(self) -> int:
        """Traite un cycle : récupère les tâches mûres et les exécute.

        Retourne le nombre de tâches traitées.
        """
        tasks = self.db.wakeup_calls.list_pending(limit=self.max_tasks_per_cycle)
        if not tasks:
            return 0

        count = 0
        for task in tasks:
            claimed = self.db.wakeup_calls.claim(task["task_id"])
            if not claimed:
                continue

            try:
                result = self._worker.execute(task["task_id"])
                logger.info("Tâche %d terminée : %s", task["task_id"], result.get("status", "?"))
                count += 1
            except Exception as e:
                logger.error("Tâche %d échouée : %s", task["task_id"], e)

        self.db.commit()
        return count
