from __future__ import annotations

import threading
from enum import Enum, auto
from typing import Any, Dict, Optional

try:
    from modules.control.pause_flag import is_paused, wait_for_resume
except Exception:  # pragma: no cover - dépendance optionnelle selon l'environnement
    is_paused = lambda **kwargs: False  # type: ignore[assignment]
    wait_for_resume = lambda **kwargs: None  # type: ignore[assignment]


class PauseScope(Enum):
    AGENT = auto()
    PROJECT = auto()
    TEAM = auto()


class PauseManager:
    """Multi-level pause manager shared across project/team/agent."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._paused: Dict[PauseScope, bool] = {
            PauseScope.AGENT: False,
            PauseScope.PROJECT: False,
            PauseScope.TEAM: False,
        }
        self._resume_event = threading.Event()

    def set_paused(self, scope: PauseScope, paused: bool) -> None:
        with self._lock:
            self._paused[scope] = paused
            if not paused:
                self._resume_event.set()

    def is_paused(self, scope: PauseScope) -> bool:
        with self._lock:
            return self._paused[scope]

    def is_globally_paused(self) -> bool:
        with self._lock:
            return any(self._paused.values())

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "agent_paused": self._paused[PauseScope.AGENT],
                "project_paused": self._paused[PauseScope.PROJECT],
                "team_paused": self._paused[PauseScope.TEAM],
                "global_paused": any(self._paused.values()),
            }

    def wait_until_resume(self, timeout: Optional[float] = None) -> bool:
        self._resume_event.clear()
        return self._resume_event.wait(timeout)


class StreamBus:
    """Simplified stream bus for SSE pausing."""

    def __init__(self, pause_manager: PauseManager) -> None:
        self._pause_manager = pause_manager
        self._paused_streams: int = 0
        self._lock = threading.RLock()

    def pause_stream(self) -> None:
        with self._lock:
            self._paused_streams += 1

    def resume_stream(self) -> None:
        with self._lock:
            if self._paused_streams > 0:
                self._paused_streams -= 1

    def is_stream_paused(self) -> bool:
        with self._lock:
            return self._pause_manager.is_globally_paused() or self._paused_streams > 0

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "stream_paused": self.is_stream_paused(),
                "paused_streams": self._paused_streams,
            }


class FSM:
    """Finite State Machine interpreter with pause integration."""

    def __init__(self, pause_manager: Optional[PauseManager] = None) -> None:
        self._paused = False
        self._pause_manager = pause_manager or PauseManager()
        self._stream_bus = StreamBus(self._pause_manager)

    @property
    def paused(self) -> bool:
        return self._paused or self._pause_manager.is_globally_paused()

    def pause(self) -> None:
        self._paused = True
        self._pause_manager.set_paused(PauseScope.AGENT, True)
        self._stream_bus.pause_stream()

    def resume(self) -> None:
        self._paused = False
        self._pause_manager.set_paused(PauseScope.AGENT, False)
        self._stream_bus.resume_stream()

    def pause_project(self) -> None:
        self._pause_manager.set_paused(PauseScope.PROJECT, True)

    def resume_project(self) -> None:
        self._pause_manager.set_paused(PauseScope.PROJECT, False)

    def pause_team(self) -> None:
        self._pause_manager.set_paused(PauseScope.TEAM, True)

    def resume_team(self) -> None:
        self._pause_manager.set_paused(PauseScope.TEAM, False)

    def is_paused(self) -> bool:
        return self.paused

    def status(self) -> Dict[str, Any]:
        return {
            "fsm_paused": self._paused,
            "pause_manager": self._pause_manager.status(),
            "stream_bus": self._stream_bus.status(),
        }

    def run_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        if self.paused:
            return {"status": "paused", "step": step.get("id")}
        return {"status": "ok", "step": step.get("id")}

    def wait_if_paused(
        self,
        project_id: Optional[str] = None,
        team_name: Optional[str] = None,
        agent_id: Optional[str] = None,
        poll_interval: float = 0.25,
        timeout: Optional[float] = None,
    ) -> None:
        """Bloque l'agent tant qu'un flag de pause global est actif.

        - Avant chaque step, le FSM appelle cette méthode.
        - Si paused, l'agent attend un événement de resume.
        - Le comportement demandé est respecté :
            * pause : l'agent finit son step courant puis se met en attente ;
            * resume : reprend au step suivant.
        """
        while is_paused(
            project_id=project_id,
            team_name=team_name,
            agent_id=agent_id,
        ):
            notified = self._pause_manager.wait_until_resume(timeout=poll_interval)
            if notified:
                break
            time.sleep(poll_interval)

    def pause_global(
        self,
        project_id: Optional[str] = None,
        team_name: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Active la pause au niveau projet/team/agent dans le store partagé.

        Cela notifie également les agents en attente via l'event de resume.
        """
        set_paused("project", project_id or "default", True)
        set_paused("team", team_name or "default", True)
        set_paused("agent", agent_id or "default", True)
        self._pause_manager.set_paused(PauseScope.PROJECT, True)
        self._pause_manager.set_paused(PauseScope.TEAM, True)
        self._pause_manager.set_paused(PauseScope.AGENT, True)
        return {"status": "ok", "paused": True}

    def resume_global(
        self,
        project_id: Optional[str] = None,
        team_name: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reprend l'exécution et réveille les agents en attente."""
        set_paused("project", project_id or "default", False)
        set_paused("team", team_name or "default", False)
        set_paused("agent", agent_id or "default", False)
        self._pause_manager.set_paused(PauseScope.PROJECT, False)
        self._pause_manager.set_paused(PauseScope.TEAM, False)
        self._pause_manager.set_paused(PauseScope.AGENT, False)
        return {"status": "ok", "paused": False}


import time  # noqa: E402 - import tardif pour wait_if_paused
