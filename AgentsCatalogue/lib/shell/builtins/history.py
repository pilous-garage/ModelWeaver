"""history — historique des commandes du shell."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_history(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    log = getattr(auth, "_shell_log", None)
    if log is None:
        return {"exit_code": 1, "stdout": "", "stderr": "pas de log disponible"}
    cmds = log.all_commands()
    if not cmds:
        return {"exit_code": 0, "stdout": "aucune commande dans l'historique\n", "stderr": ""}
    lines = []
    for i, c in enumerate(cmds, 1):
        cmd_text = c.get("cmd", "")
        status = c.get("result", {}).get("exit_code", "?")
        lines.append(f"{i:>5}  {status}  {cmd_text}")
    return {"exit_code": 0, "stdout": "\n".join(lines) + "\n", "stderr": ""}