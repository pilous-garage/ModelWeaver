"""which — localisation d'une commande."""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_which(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: which <cmd> [...]"}
    results = []
    for cmd in args:
        path = shutil.which(cmd)
        if path:
            results.append(path)
        else:
            return {"exit_code": 1, "stdout": "", "stderr": f"commande introuvable : {cmd}"}
    return {"exit_code": 0, "stdout": "\n".join(results) + "\n", "stderr": ""}