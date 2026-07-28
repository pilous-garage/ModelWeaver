"""chown — changer le propriétaire d'un fichier (root only)."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_chown(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if len(args) < 2:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: chown <user> <path> [...]"}
    user_str = args[0]
    try:
        uid = int(user_str)
    except ValueError:
        import pwd
        try:
            uid = pwd.getpwnam(user_str).pw_uid
        except KeyError:
            return {"exit_code": 1, "stdout": "", "stderr": f"utilisateur introuvable : {user_str}"}
    for path_arg in args[1:]:
        target = auth.check_path(Path(workdir) / path_arg)
        if not target.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"introuvable : {target}"}
        try:
            os.chown(target, uid, -1)
        except PermissionError:
            return {"exit_code": 1, "stdout": "", "stderr": f"permission refusée (root only) : {target}"}
    return {"exit_code": 0, "stdout": "", "stderr": ""}