"""source — exécute un fichier de commandes dans le shell courant."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_source(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: source <path>"}
    path = Path(args[0])
    if not path.is_absolute():
        path = Path(workdir) / path
    if not path.exists():
        return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {path}"}
    return {"exit_code": 0, "stdout": f"sourcing {path}…\n", "stderr": ""}