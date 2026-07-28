"""sed — éditeur de flux (Python natif, multi-plateforme)."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_sed(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    script = ""
    filename = None
    inplace = False
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-e") and len(a) > 2:
            script = a[2:]
            i += 1
        elif a == "-e" and i + 1 < len(args):
            script = args[i + 1]
            i += 2
        elif a == "-i":
            inplace = True
            i += 1
        elif a == "-n":
            i += 1
        else:
            filename = a
            i += 1

    if not script:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: sed -e SCRIPT [file]"}

    if filename:
        target = auth.check_path(Path(workdir) / filename)
        if not target.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {filename}"}
        content = target.read_text(encoding="utf-8")
    else:
        content = stdin or ""

    lines = content.splitlines(keepends=True)
    result_lines = []
    for line in lines:
        try:
            sub = re.search(script, line)
            if sub:
                result_lines.append(line)
        except re.error:
            return {"exit_code": 1, "stdout": "", "stderr": f"expression régulière invalide : {script}"}

    result = "".join(result_lines)

    if inplace and filename and result:
        target.write_text(result, encoding="utf-8")

    return {"exit_code": 0, "stdout": result, "stderr": ""}