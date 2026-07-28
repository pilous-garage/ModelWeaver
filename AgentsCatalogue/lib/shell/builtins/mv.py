"""mv — déplacer / renommer un fichier."""

from pathlib import Path
from shutil import move
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_mv(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if len(args) < 2:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: mv <source> <dest> [...]"}
    for src_arg in args[:-1]:
        src = auth.check_path(Path(workdir) / src_arg)
        if not src.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"source introuvable : {src}"}
    dest_arg = args[-1]
    dest = auth.check_path(Path(workdir) / dest_arg)
    if len(args) == 2 and dest.is_dir():
        dest = dest / Path(args[0]).name
        dest.parent.mkdir(parents=True, exist_ok=True)
    for src_arg in args[:-1]:
        src = auth.check_path(Path(workdir) / src_arg)
        move(str(src), str(dest))
    return {"exit_code": 0, "stdout": "", "stderr": ""}