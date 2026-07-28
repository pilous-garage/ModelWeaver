"""ShellLog — persistance et trace d'une session shell."""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class ShellLog:
    """Tracer et persister les commandes d'un shell."""

    def __init__(self, shell_id: str, workdir: Path):
        self.shell_id = shell_id
        self.workdir = workdir
        self._commands: List[dict] = []
        self._events: List[dict] = []
        self._last_error: Optional[str] = None
        self._env: Dict[str, str] = {}
        self.state_file = workdir / "shells" / shell_id / "state.json"
        self._loaded = False

    # ── sérialisation / persistance ────────────────────

    def _ensure_state_dir(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self._ensure_state_dir()
        payload = {
            "shell_id": self.shell_id,
            "workdir": str(self.workdir),
            "commands": self._commands,
            "events": self._events,
            "last_error": self._last_error,
            "env": self._env,
        }
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)

    def load(self) -> bool:
        if self._loaded:
            return True
        if not self.state_file.exists():
            return False
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._commands = data.get("commands", [])
            self._events = data.get("events", [])
            self._last_error = data.get("last_error")
            self._env = data.get("env", {})
            self._loaded = True
            return True
        except (json.JSONDecodeError, OSError):
            return False

    # ── événements ──────────────────────────────────────

    def append_event(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        self._events.append({
            "ts": time.time(),
            "event": event,
            "data": data or {},
        })

    # ── commandes ───────────────────────────────────────

    def start_command(self, cmd: str) -> str:
        cmd_id = f"cmd_{int(time.time() * 1000)}"
        entry = {
            "cmd_id": cmd_id,
            "cmd": cmd,
            "started_at": time.time(),
        }
        self._commands.append(entry)
        return cmd_id

    def end_command(self, cmd_id: str, result: Dict[str, Any]) -> None:
        for c in reversed(self._commands):
            if c["cmd_id"] == cmd_id:
                c["ended_at"] = time.time()
                c["duration_ms"] = (c["ended_at"] - c["started_at"]) * 1000
                c["result"] = result
                if result.get("status") == "error":
                    self._last_error = result.get("error", "unknown error")
                break

    # ── inspect ─────────────────────────────────────────

    @property
    def command_count(self) -> int:
        return len(self._commands)

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def get_command(self, cmd_id: str) -> Optional[dict]:
        for c in self._commands:
            if c["cmd_id"] == cmd_id:
                return c
        return None

    def tail(self, n: int = 20) -> List[dict]:
        return list(self._commands[-n:])

    def all_commands(self) -> List[dict]:
        return list(self._commands)

    def all_events(self) -> List[dict]:
        return list(self._events)