"""AsyncTicker — Boucle asynchrone de surveillance des services.

Le Ticker ne gère PAS les agents. Il surveille les services système
et réveille l'AgentManager à chaque cycle.

Cycle :
  1. AgentManager.tick() → vérifie les heartbeats, nettoie les zombies
  2. Attend le prochain cycle (poll_interval)
  3. 0% CPU au repos (asyncio.Event)
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("modelweaver.ticker")


class AsyncTicker:
    """Ticker asynchrone avec wake-up immédiat.

    Seul point de contact avec les agents : AgentManager.
    Ne voit jamais les agents directement.
    """

    def __init__(
        self,
        agent_manager: Optional["AgentManager"] = None,
        poll_interval: float = 5.0,
    ):
        if agent_manager is None:
            from services.agent_manager.service import AgentManager
            agent_manager = AgentManager()
        self.agent_manager = agent_manager
        self.poll_interval = poll_interval
        self._wake_event = asyncio.Event()
        self._running = False

    def wakeup(self):
        """Déclenche un réveil immédiat (thread-safe)."""
        self._wake_event.set()

    async def start(self):
        """Boucle principale — tourne jusqu'à stop()."""
        self._running = True

        logger.info("Ticker démarré (poll=%ss)", self.poll_interval)

        while self._running:
            # Seul point de contact : AgentManager
            try:
                result = self.agent_manager.tick()
                if result["zombies_found"] > 0:
                    logger.warning(
                        "AgentManager : %d zombies nettoyés",
                        result["zombies_found"],
                    )
            except Exception as e:
                logger.error("AgentManager.tick() échoué : %s", e)

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
