"""sed — éditeur de flux (Python natif, multi-plateforme)."""

import re
import sys
from typing import List, Optional


def execute(args: List[str], stdin: Optional[str], workdir: str) -> dict:
    script = ""
    filename = None
    i = 0
    while i < len(args):
        if args[i].startswith("-e") and len(args[i]) > 2:
            script = args[i][2:]
            i += 1
        elif args[i] == "-e" and i + 1 < len(args):
            script = args[i + 1]
            i += 2
        elif args[i] == "-n":
            i += 1
        else:
            filename = args[i]
            i += 1

    if not script:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: sed -e SCRIPT [file]"}

    content: str = stdin or ""
    try:
        with open(filename, encoding="utf-8") as f:
            content = f.read()
    except (FileNotFoundError, IsADirectoryError):
        if filename:
            return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {filename}"}

    lines = content.splitlines(keepends=True)
    result = ""
    for line in lines:
        if re.search(script, line):
            result += line
    if not result.endswith("\n") and result:
        result += "\n"
    return {"exit_code": 0, "stdout": result, "stderr": ""}