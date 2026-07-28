"""cp — copie de fichier."""

from pathlib import Path
from shutil import copy2
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_cp(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if len(args) < 2:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: cp <source> <dest> [...]"}
    for src_arg in args[:-1]:
        src = auth.check_path(Path(workdir) / src_arg)
        if not src.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"source introuvable : {src}"}
        if not src.is_file():
            return {"exit_code": 1, "stdout": "", "stderr": f"pas un fichier : {src}"}
    dest_arg = args[-1]
    dest = auth.check_path(Path(workdir) / dest_arg)
    if len(args) == 2 and dest.is_dir():
        dest = dest / Path(args[0]).name
    for src_arg in args[:-1]:
        src = auth.check_path(Path(workdir) / src_arg)
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy2(str(src), str(dest))
    return {"exit_code": 0, "stdout": "", "stderr": ""}