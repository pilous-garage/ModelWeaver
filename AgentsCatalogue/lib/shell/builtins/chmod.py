"""chmod — changer les permissions d'un fichier."""

import os
import stat as _stat
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_chmod(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if len(args) < 2:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: chmod <mode> <path> [...]"}
    mode_str = args[0]
    try:
        mode = int(mode_str, 8)
    except ValueError:
        return {"exit_code": 1, "stdout": "", "stderr": f"mode invalide : {mode_str}"}
    for path_arg in args[1:]:
        target = auth.check_path(Path(workdir) / path_arg)
        if not target.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"introuvable : {target}"}
        os.chmod(target, mode)
    return {"exit_code": 0, "stdout": "", "stderr": ""}