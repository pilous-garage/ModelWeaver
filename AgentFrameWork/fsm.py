from __future__ import annotations

import threading
from enum import Enum, auto
from typing import Any, Dict, Optional


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

    def set_paused(self, scope: PauseScope, paused: bool) -> None:
        with self._lock:
            self._paused[scope] = paused

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
