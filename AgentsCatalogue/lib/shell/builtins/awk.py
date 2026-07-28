"""awk — traitement de texte (Python natif, multi-plateforme)."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_awk(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: awk 'PATTERN {action}' [file]"}
    program = args[0]
    filename = args[1] if len(args) > 1 else None

    if filename:
        target = auth.check_path(Path(workdir) / filename)
        if not target.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {filename}"}
        content = target.read_text(encoding="utf-8")
    else:
        content = stdin or ""

    lines = content.splitlines()
    result_lines = []
    for line in lines:
        fields = line.split()
        vars = {
            "NF": len(fields),
            "NR": len(result_lines) + 1,
            "$0": line,
        }
        for idx, f in enumerate(fields, 1):
            vars[f"${idx}"] = f
            vars[f"${idx}"] = int(f) if f.isdigit() else f

        match = False
        pattern = program.split()[0] if " " in program else program
        action = None
        if " " in program:
            parts = program.split(None, 1)
            pattern = parts[0]
            action = parts[1] if len(parts) > 1 else None

        try:
            if pattern.startswith("/") and pattern.endswith("/"):
                pat = pattern[1:-1]
                match = bool(re.search(pat, line))
            elif pattern == "1":
                match = True
            elif pattern.isdigit():
                match = (len(result_lines) + 1) == int(pattern)
            else:
                match = bool(re.search(pattern, line))
        except re.error:
            match = False

        if match:
            result_lines.append(line)

    return {"exit_code": 0, "stdout": "\n".join(result_lines) + ("\n" if result_lines else ""), "stderr": ""}