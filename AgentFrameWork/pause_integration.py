"""PauseIntegration — Helper d'intégration du flag de pause dans le FSM et le bridge.

Deux responsabilités :
  - Construire un `signal_check` compatible FSMInterpreter à partir d'un scope
    projet/team/agent.
  - Fournir un wrapper de `chat_stream` interrompable quand le flag passe en pause.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional

from modules.control.pause_flag import get_pause_store, PauseFlagStore

logger = logging.getLogger("modelweaver.control.pause_integration")


class PauseSignalError(Exception):
    """Signal de pause propagé vers le FSM."""


def build_signal_check(
    project_id: Optional[str] = None,
    team_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    pause_store: Optional[PauseFlagStore] = None,
) -> Callable[[Any], None]:
    """Fabrique un `signal_check(result)` pour FSMInterpreter.

    Avant chaque step, le FSM appelle ce callable. Si un flag actif est
    détecté, il positionne `result._paused = True` (et peut lever
    PauseSignalError pour forcer une interruption immédiate).
    """
    store = pause_store or get_pause_store()

    def signal_check(result: Any) -> None:
        effective = store.effective_state(
            project_id=project_id,
            team_id=team_id,
            agent_id=agent_id,
        )
        if effective.get("paused"):
            result._paused = True
            result.status = "paused"
            result.end_reason = effective.get("reason") or "Pause active"
            logger.info(
                "Pause signal reçu : scope=%s/%s reason=%s",
                effective.get("scope"),
                effective.get("scope_id"),
                effective.get("reason"),
            )
            raise PauseSignalError(effective.get("reason") or "pause")

    return signal_check


class PauseAwareStreamWrapper:
    """Wrapper d'un itérateur de stream SSE qui s'interrompt si le flag est activé.

    Usage :
        stream = bridge.chat_stream(...)
        for chunk in PauseAwareStreamWrapper(stream, pause_check):
            stream_sink(chunk)
    """

    def __init__(
        self,
        stream: Iterator[str],
        pause_check: Optional[Callable[[], bool]] = None,
        poll_interval: float = 0.2,
    ) -> None:
        self._stream = stream
        self._pause_check = pause_check
        self._poll_interval = poll_interval
        self._paused = threading.Event()
        self._stopped = threading.Event()

    def __iter__(self) -> Iterator[str]:
        for chunk in self._stream:
            if self._stopped.is_set():
                break
            if self._pause_check and self._pause_check():
                self._paused.set()
                logger.info("Stream interrompu par pause flag, en attente de resume...")
                while self._pause_check() and not self._stopped.is_set():
                    time.sleep(self._poll_interval)
                if self._stopped.is_set():
                    break
                logger.info("Stream repris après pause")
            yield chunk

    def stop(self) -> None:
        """Arrête définitivement le stream (kill)."""
        self._stopped.set()
        self._paused.set()


def wrap_chat_stream_with_pause(
    stream: Iterator[str],
    project_id: Optional[str] = None,
    team_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    pause_store: Optional[PauseFlagStore] = None,
    poll_interval: float = 0.2,
) -> PauseAwareStreamWrapper:
    """Wrapper prêt à l'emploi pour un stream SSE.

    Construit automatiquement le `pause_check` à partir du scope fourni.
    """
    store = pause_store or get_pause_store()

    def pause_check() -> bool:
        effective = store.effective_state(
            project_id=project_id,
            team_id=team_id,
            agent_id=agent_id,
        )
        return bool(effective.get("paused"))

    return PauseAwareStreamWrapper(stream, pause_check=pause_check, poll_interval=poll_interval)
