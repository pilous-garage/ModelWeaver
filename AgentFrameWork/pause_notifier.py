"""PauseNotifier — Mécanisme de notification pour réveiller les agents en attente.

Utilise un `threading.Condition` pour bloquer/réveiller les threads qui
attendent la fin d'une pause. Chaque changement de flag de pause notifie
tous les threads en attente.
"""

from __future__ import annotations

import threading
from typing import Optional


class PauseNotifier:
    """Notificateur global de pause/resume.

    Un seul condition par processus ; les lecteurs s'y endorment et sont
    réveillés en batch quand un flag change.
    """

    _instance: Optional[PauseNotifier] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._condition = threading.Condition()

    @classmethod
    def get_instance(cls) -> PauseNotifier:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def wait(self, is_paused_check: Any, poll_interval: float = 0.25) -> None:
        """Bloque tant que `is_paused_check()` retourne True.

        `is_paused_check` est un callable sans argument qui lit l'état du
        flag de pause partagé (ex: `pause_flag_store.is_paused`).
        """
        with self._condition:
            while is_paused_check():
                self._condition.wait(timeout=poll_interval)

    def notify_all(self) -> None:
        """Réveille tous les threads en attente.

        À appeler après chaque changement de flag de pause.
        """
        with self._condition:
            self._condition.notify_all()
