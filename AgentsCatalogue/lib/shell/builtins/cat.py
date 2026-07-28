"""cat — affichage de fichier."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_cat(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: cat <path> [...]"}
    outputs = []
    for path_arg in args:
        target = auth.check_path(Path(workdir) / path_arg)
        if not target.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {target}"}
        if not target.is_file():
            return {"exit_code": 1, "stdout": "", "stderr": f"pas un fichier : {target}"}
        outputs.append(target.read_text(encoding="utf-8"))
    return {"exit_code": 0, "stdout": "\n".join(outputs), "stderr": ""}