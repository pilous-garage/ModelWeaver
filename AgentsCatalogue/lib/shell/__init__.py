"""Shell interne pour agents LLM.

Interface publique : get_shell(), list_shells()."""

import json
import os
import uuid
from pathlib import Path
from typing import List, Optional

from .auth import ShellAuth, AuthorizationError, VFSPathError
from .auth_request import AuthorizationRequest, request_handler
from .executor import ExecuteError
from .log import ShellLog
from .shell import Shell, ShellState


def get_shell(
    workdir: Optional[str] = None,
    home_root: Optional[Path] = None,
    allowed_roots: Optional[list] = None,
    allowed_commands: Optional[set] = None,
    load_session: Optional[str] = None,
) -> Shell:
    """Crée et ouvre un shell pour un agent.

    Args:
        workdir: répertoire de travail (défaut: home_root / "sessions" / agent_id)
        home_root: racine du VFS agent (défaut: ~/.modelweaver)
        allowed_roots: racines VFS autorisées (défaut: [home_root])
        allowed_commands: whitelist de commandes système autorisées en fallback
        load_session: shell_id d'une session persistée à recharger"""
    if home_root is None:
        home_root = Path.home() / ".modelweaver"

    if workdir is None:
        workdir = str(home_root / "sessions")

    if load_session:
        shell_id = load_session
    else:
        shell_id = uuid.uuid4().hex[:12]

    auth = ShellAuth(
        home_root=home_root,
        allowed_roots=allowed_roots,
        allowed_commands=allowed_commands,
    )
    shell = Shell(shell_id=shell_id, workdir=workdir, auth=auth)
    shell.open()

    if load_session:
        shell.log.load()
        shell._env = dict(shell.log._env)

    return shell


def list_shells(home_root: Optional[Path] = None) -> List[dict]:
    """Liste les sessions shell persistantes dans le répertoire home."""
    if home_root is None:
        home_root = Path.home() / ".modelweaver"
    sessions_dir = home_root / "shells"
    if not sessions_dir.exists():
        return []
    results = []
    for session_dir in sorted(sessions_dir.iterdir()):
        if not session_dir.is_dir():
            continue
        state_file = session_dir / "state.json"
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                results.append({
                    "shell_id": session_dir.name,
                    "workdir": data.get("workdir", ""),
                    "command_count": len(data.get("commands", [])),
                    "last_modified": os.path.getmtime(str(state_file)),
                })
            except (json.JSONDecodeError, OSError):
                continue
    return results


__all__ = [
    "get_shell",
    "list_shells",
    "Shell",
    "ShellState",
    "ShellAuth",
    "ShellLog",
    "ExecuteError",
    "AuthorizationError",
    "AuthorizationRequest",
    "request_handler",
    "VFSPathError",
]