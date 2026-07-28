"""unalias — supprimer un alias."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_unalias(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    aliases = getattr(auth, "_shell_aliases", {})
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: unalias <name> [...]"}
    for arg in args:
        aliases.pop(arg, None)
    return {"exit_code": 0, "stdout": "", "stderr": ""}