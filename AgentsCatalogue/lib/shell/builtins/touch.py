"""touch — créer ou mettre à jour un fichier."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_touch(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: touch <path> [...]"}
    for path_arg in args:
        target = auth.check_path(Path(workdir) / path_arg)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.touch()
        else:
            target.stat()
    return {"exit_code": 0, "stdout": "", "stderr": ""}