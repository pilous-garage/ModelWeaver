"""unset — supprimer une variable d'environnement du shell."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_unset(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: unset <VAR> [...]"}
    if hasattr(auth, "_shell_env") and auth._shell_env is not None:
        for var in args:
            auth._shell_env.pop(var, None)
    return {"exit_code": 0, "stdout": "", "stderr": ""}