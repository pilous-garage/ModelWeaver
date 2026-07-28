"""Shell interne pour agents LLM."""

import os
import time
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from .log import ShellLog
from .auth import ShellAuth


class ShellState(Enum):
    CREATED = "created"
    OPEN = "open"
    RUNNING = "running"
    SUSPENDED = "suspended"
    CLOSED = "closed"
    ERROR = "error"


class Shell:
    def __init__(
        self,
        shell_id: str,
        workdir: str,
        auth: ShellAuth,
    ):
        self.shell_id = shell_id
        self.workdir = Path(workdir).resolve()
        self.auth = auth
        self.state = ShellState.CREATED
        self.log = ShellLog(shell_id, self.workdir)
        self._env: Dict[str, str] = {}
        self._aliases: Dict[str, str] = {}
        self._last_exit_code: int = 0
        self._bg_jobs: Dict[int, dict] = {}  # job_id → {"pid", "cmd", "status"}
        self._created_at = time.time()
        self._closed_at: Optional[float] = None

    # ── cycle de vie ──────────────────────────────────────────

    def open(self) -> "Shell":
        if self.state != ShellState.CREATED:
            raise RuntimeError(
                f"shell '{self.shell_id}' déjà dans l'état {self.state.value}"
            )
        self.state = ShellState.OPEN
        self.log.append_event("open", {"workdir": str(self.workdir)})
        return self

    def run(self, cmd: str) -> dict:
        if self.state not in (ShellState.OPEN, ShellState.SUSPENDED):
            raise RuntimeError(
                f"shell '{self.shell_id}' dans l'état {self.state.value}, "
                f"pas exécutable (attendu open/suspended)"
            )
        self.state = ShellState.RUNNING
        cmd_id = self.log.start_command(cmd)
        result = {"cmd_id": cmd_id, "cmd": cmd}
        try:
            out = self._execute(cmd)
            result.update(out)
            self._apply_result(out)
            self._last_exit_code = out.get("exit_code", self._last_exit_code)
            self.log._env = dict(self._env)
            result["status"] = "success"
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            self.state = ShellState.ERROR
        finally:
            self.log.end_command(cmd_id, result)
            if self.state in (ShellState.ERROR, ShellState.RUNNING):
                self.state = ShellState.OPEN
        return result

    def _apply_result(self, out: dict) -> None:
        if "_cd_to" in out:
            new_wd = Path(out["_cd_to"]).resolve()
            try:
                self.auth.check_path(new_wd)
                self.workdir = new_wd
            except Exception:
                pass

    def close(self) -> dict:
        if self.state == ShellState.CLOSED:
            return {"status": "already_closed", "shell_id": self.shell_id}
        self.state = ShellState.CLOSED
        self._closed_at = time.time()
        self.log.append_event("close", {"duration_s": self._closed_at - self._created_at})
        return {"status": "closed", "shell_id": self.shell_id, "commands_executed": self.log.command_count}

    def suspend(self) -> dict:
        if self.state != ShellState.OPEN:
            raise RuntimeError(f"shell '{self.shell_id}' non ouvert (état: {self.state.value})")
        self.state = ShellState.SUSPENDED
        self.log.append_event("suspend", {})
        return {"status": "suspended", "shell_id": self.shell_id}

    def resume(self) -> dict:
        if self.state != ShellState.SUSPENDED:
            raise RuntimeError(f"shell '{self.shell_id}' non suspendu (état: {self.state.value})")
        self.state = ShellState.OPEN
        self.log.append_event("resume", {})
        return {"status": "resumed", "shell_id": self.shell_id}

    # ── internals ─────────────────────────────────────────────

    def _execute(self, cmd: str) -> dict:
        from .executor import ShellExecutor

        executor = ShellExecutor(
            workdir=str(self.workdir),
            auth=self.auth,
            shell_id=self.shell_id,
            env=self._env,
            log=self.log,
            aliases=self._aliases,
            last_exit_code=self._last_exit_code,
            bg_jobs=self._bg_jobs,
        )
        return executor.execute(cmd)

    # ── inspect ────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "shell_id": self.shell_id,
            "state": self.state.value,
            "workdir": str(self.workdir),
            "created_at": self._created_at,
            "closed_at": self._closed_at,
            "commands_executed": self.log.command_count,
            "last_error": self.log.last_error,
            "env_vars": len(self._env),
        }

    def tail(self, n: int = 20) -> List[dict]:
        return self.log.tail(n)

    def get_command(self, cmd_id: str) -> Optional[dict]:
        return self.log.get_command(cmd_id)