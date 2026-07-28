"""diff — comparaison de fichiers."""

from pathlib import Path
from difflib import unified_diff
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_diff(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if len(args) != 2:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: diff <file1> <file2>"}
    f1 = auth.check_path(Path(workdir) / args[0])
    f2 = auth.check_path(Path(workdir) / args[1])
    for f in (f1, f2):
        if not f.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {f}"}
        if not f.is_file():
            return {"exit_code": 1, "stdout": "", "stderr": f"pas un fichier : {f}"}

    lines1 = f1.read_text(encoding="utf-8").splitlines()
    lines2 = f2.read_text(encoding="utf-8").splitlines()
    diff = list(unified_diff(lines1, lines2, fromfile=args[0], tofile=args[1], lineterm=""))
    return {"exit_code": 0, "stdout": "\n".join(diff) + ("\n" if diff else ""), "stderr": ""}