"""ProcessTracker — suivi des processus lancés par chaque agent."""

import os
import time
from typing import Dict, List, Optional


class _ProcessTracker:

    def __init__(self):
        self._processes: Dict[int, dict] = {}

    def register(self, pid: int, agent_id: Optional[str], team_id: Optional[str], cmd: str) -> None:
        self._processes[pid] = {
            "agent_id": agent_id,
            "team_id": team_id,
            "cmd": cmd,
            "launched_at": time.time(),
        }

    def unregister(self, pid: int) -> None:
        self._processes.pop(pid, None)

    def is_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def get(self, pid: int) -> Optional[dict]:
        info = self._processes.get(pid)
        if info is not None:
            info = {**info, "alive": self.is_alive(pid)}
        return info

    def processes_by_agent(self, agent_id: str) -> List[dict]:
        return [
            {"pid": pid, **data, "alive": self.is_alive(pid)}
            for pid, data in self._processes.items()
            if data["agent_id"] == agent_id
        ]

    def processes_by_team(self, team_id: str) -> List[dict]:
        return [
            {"pid": pid, **data, "alive": self.is_alive(pid)}
            for pid, data in self._processes.items()
            if data["team_id"] == team_id
        ]

    def all(self) -> List[dict]:
        return [
            {"pid": pid, **data, "alive": self.is_alive(pid)}
            for pid, data in self._processes.items()
        ]

    def kill(self, pid: int) -> bool:
        if pid in self._processes:
            del self._processes[pid]
            return True
        return False

    def clear(self) -> None:
        self._processes.clear()


tracker = _ProcessTracker()