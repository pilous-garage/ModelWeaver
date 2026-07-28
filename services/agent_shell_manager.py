"""AgentShellManager — Registre global des shells par agent + cycle de vie.

Chaque agent LLM peut avoir son propre shell interne.
Le manager garantit qu'un agent a exactement un shell,
persiste l'état à la fermeture, et permet le routage
des authorization requests vers l'agent.

Usage :
    from services.agent_shell_manager import agent_shell_manager
    sh = agent_shell_manager.get_or_create("agent-42", team_id="team-a", role="member")
    result = sh.run("ls /workspace")
    agent_shell_manager.close("agent-42")
"""

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from AgentsCatalogue.lib.shell import (
    Shell, ShellAuth, ShellState, get_shell, list_shells,
)
from AgentsCatalogue.lib.shell.auth_request import (
    AuthorizationRequest, RequestType, request_handler,
)


# ── type pour les callbacks LLM ──────────────────────────────────

AuthCallback = Callable[[AuthorizationRequest], None]


class AgentShell:
    """Wrapper autour d'un Shell avec contexte agent.

    Lie le shell à l'agent : identité, permissions, cycle de vie."""

    def __init__(
        self,
        agent_id: str,
        team_id: Optional[str],
        role: str = "member",
        home_root: Optional[Path] = None,
        allowed_commands: Optional[set] = None,
    ):
        self.agent_id = agent_id
        self.team_id = team_id
        self.role = role
        self.home_root = (home_root or Path.home() / ".modelweaver").resolve()
        self.allowed_commands = allowed_commands
        self.status = "stopped"
        self._shell: Optional[Shell] = None
        self._created_at: Optional[float] = None

    # ── cycle de vie ─────────────────────────────────

    def start(self) -> Shell:
        if self._shell is not None:
            return self._shell

        auth = ShellAuth(
            home_root=self.home_root,
            agent_id=self.agent_id,
            team_id=self.team_id,
            role=self.role,
            allowed_commands=self.allowed_commands,
        )
        workdir = self.home_root / "work"
        workdir.mkdir(parents=True, exist_ok=True)

        self._shell = get_shell(
            workdir=str(workdir),
            home_root=self.home_root,
        )
        self._shell.auth = auth  # inject agent context
        self._shell._env = {}  # isolated env
        self.status = "running"
        self._created_at = time.time()
        return self._shell

    def stop(self) -> None:
        if self._shell is None:
            return
        try:
            self._shell.log.save()
            self._shell.close()
        except Exception:
            pass
        self.status = "stopped"
        self._shell = None

    def restart(self) -> Shell:
        self.stop()
        return self.start()

    # ── exécution ────────────────────────────────────

    def run(self, cmd: str) -> dict:
        if self._shell is None:
            raise RuntimeError(f"AgentShell '{self.agent_id}' non démarré (appelez start())")
        return self._shell.run(cmd)

    # ── accès ────────────────────────────────────────

    @property
    def shell(self) -> Optional[Shell]:
        return self._shell

    def health(self) -> dict:
        if self._shell is None:
            return {"ok": False, "agent_id": self.agent_id, "status": "stopped"}
        return {
            "ok": self._shell.state == ShellState.OPEN,
            "agent_id": self.agent_id,
            "team_id": self.team_id,
            "role": self.role,
            "status": self.status,
            "state": self._shell.state.value,
            "workdir": str(self._shell.workdir),
            "commands_executed": self._shell.log.command_count,
            "uptime_s": time.time() - (self._created_at or time.time()),
        }


class AgentShellManager:
    """Registre singleton des shells par agent.

    Chaque agent a exactement un shell.
    Gère le routage des authorization requests vers l'agent LLM."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._shells: Dict[str, AgentShell] = {}
                    cls._instance._initialized = False
                    cls._instance._auth_callbacks: List[AuthCallback] = []
        return cls._instance

    def init(self):
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._register_auth_handler()
            self._initialized = True

    # ── registre ─────────────────────────────────────

    def get_or_create(
        self,
        agent_id: str,
        team_id: Optional[str] = None,
        role: str = "member",
        home_root: Optional[Path] = None,
        allowed_commands: Optional[set] = None,
    ) -> AgentShell:
        existing = self._shells.get(agent_id)
        if existing is not None:
            if existing.status == "stopped":
                existing.start()
            return existing

        ag_sh = AgentShell(
            agent_id=agent_id,
            team_id=team_id,
            role=role,
            home_root=home_root,
            allowed_commands=allowed_commands,
        )
        ag_sh.start()
        self._shells[agent_id] = ag_sh
        return ag_sh

    def get(self, agent_id: str) -> Optional[AgentShell]:
        return self._shells.get(agent_id)

    def close(self, agent_id: str) -> bool:
        ag_sh = self._shells.pop(agent_id, None)
        if ag_sh is None:
            return False
        ag_sh.stop()
        return True

    def close_all(self) -> None:
        for agent_id in list(self._shells.keys()):
            self.close(agent_id)

    def list(self) -> List[dict]:
        return [
            ag_sh.health() for ag_sh in self._shells.values()
        ]

    # ── routage auth → LLM ──────────────────────────

    def register_auth_callback(self, callback: AuthCallback) -> None:
        """Enregistre un callback appelé pour chaque demande PENDING_USER."""
        self._auth_callbacks.append(callback)

    def _register_auth_handler(self) -> None:
        """Handler interne qui route les PENDING_USER vers le callback LLM."""
        def _handler(req: AuthorizationRequest) -> None:
            if req.request_type != RequestType.PENDING_USER:
                return
            for cb in self._auth_callbacks:
                try:
                    cb(req)
                except Exception:
                    pass
        request_handler.register_handler(_handler)


# ── singleton module ──────────────────────────────────────────────

agent_shell_manager = AgentShellManager()