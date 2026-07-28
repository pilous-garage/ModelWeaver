"""env — affichage de l'environnement (global + shell isolé)."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_env(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    combined = dict(os.environ)
    shell_env = getattr(auth, "_shell_env", {})
    if shell_env:
        combined.update(shell_env)
    lines = []
    for key in sorted(combined.keys()):
        lines.append(f"{key}={combined[key]}")
    return {"exit_code": 0, "stdout": "\n".join(lines) + ("\n" if lines else ""), "stderr": ""}