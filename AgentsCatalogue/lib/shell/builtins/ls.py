"""ls — liste de répertoires et fichiers."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_ls(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        target = auth.check_path(Path(workdir))
        if not target.is_dir():
            return {"exit_code": 1, "stdout": "", "stderr": f"pas un répertoire : {target}"}
        lines = []
        for entry in sorted(target.iterdir()):
            name = entry.name
            if entry.is_dir():
                name += "/"
            lines.append(name)
        return {"exit_code": 0, "stdout": "\n".join(lines) + ("\n" if lines else ""), "stderr": ""}

    lines = []
    for path_arg in args:
        target = auth.check_path(Path(workdir) / path_arg)
        if target.is_dir():
            for entry in sorted(target.iterdir()):
                name = entry.name
                if entry.is_dir():
                    name += "/"
                lines.append(f"{path_arg}/{name}" if len(args) > 1 else name)
        elif target.exists():
            lines.append(path_arg if len(args) > 1 else path_arg.split("/")[-1])
        else:
            return {"exit_code": 1, "stdout": "", "stderr": f"introuvable : {target}"}
    return {"exit_code": 0, "stdout": "\n".join(lines) + ("\n" if lines else ""), "stderr": ""}