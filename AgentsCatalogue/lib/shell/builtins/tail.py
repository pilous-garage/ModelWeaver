"""tail — dernières lignes d'un fichier."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_tail(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    n = 10
    filename = None
    i = 0
    while i < len(args):
        if args[i] == "-n" and i + 1 < len(args):
            try:
                n = int(args[i + 1])
            except ValueError:
                return {"exit_code": 1, "stdout": "", "stderr": f"invalid number: {args[i+1]}"}
            i += 2
        else:
            filename = args[i]
            i += 1
    if filename is None:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: tail [-n N] <path>"}
    target = auth.check_path(Path(workdir) / filename)
    if not target.exists():
        return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {target}"}
    lines = target.read_text(encoding="utf-8").splitlines()
    return {"exit_code": 0, "stdout": "\n".join(lines[-n:]) + ("\n" if lines[-n:] else ""), "stderr": ""}