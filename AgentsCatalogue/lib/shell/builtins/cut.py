"""cut — extraction de colonnes."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_cut(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    delimiter = "\t"
    fields: List[int] = []
    filename = None
    i = 0
    while i < len(args):
        if args[i] == "-d" and i + 1 < len(args):
            delimiter = args[i + 1]
            i += 2
        elif args[i].startswith("-f") and len(args[i]) > 2:
            try:
                fields = [int(x) for x in args[i][2:].split(",")]
            except ValueError:
                return {"exit_code": 1, "stdout": "", "stderr": f"invalid field: {args[i]}"}
            i += 1
        elif args[i] == "-f" and i + 1 < len(args):
            try:
                fields = [int(x) for x in args[i + 1].split(",")]
            except ValueError:
                return {"exit_code": 1, "stdout": "", "stderr": f"invalid field: {args[i+1]}"}
            i += 2
        else:
            filename = args[i]
            i += 1
    if not fields:
        return {"exit_code": 1, "stdout": "", "stderr": "no fields specified (use -f N[,N...])"}
    content = stdin or ""
    if filename:
        target = auth.check_path(Path(workdir) / filename)
        if not target.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {target}"}
        content = target.read_text(encoding="utf-8")
    lines = content.splitlines()
    result = []
    for line in lines:
        parts = line.split(delimiter)
        extracted = []
        for f in fields:
            if 1 <= f <= len(parts):
                extracted.append(parts[f - 1])
        result.append(delimiter.join(extracted))
    return {"exit_code": 0, "stdout": "\n".join(result) + ("\n" if result else ""), "stderr": ""}