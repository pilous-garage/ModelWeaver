"""rmdir — supprimer un répertoire vide."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_rmdir(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: rmdir <path> [...]"}
    for path_arg in args:
        target = auth.check_path(Path(workdir) / path_arg)
        if not target.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"introuvable : {target}"}
        if not target.is_dir():
            return {"exit_code": 1, "stdout": "", "stderr": f"pas un répertoire : {target}"}
        try:
            target.rmdir()
        except OSError:
            return {"exit_code": 1, "stdout": "", "stderr": f"répertoire non vide : {target}"}
    return {"exit_code": 0, "stdout": "", "stderr": ""}