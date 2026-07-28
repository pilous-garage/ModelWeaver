"""awk — traitement de texte (Python natif, multi-plateforme)."""

import sys
from typing import List, Optional


def execute(args: List[str], stdin: Optional[str], workdir: str) -> dict:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: awk PROGRAM [file]"}
    program = args[0]
    filename = args[1] if len(args) > 1 else None
    content: str = stdin or ""
    if filename:
        try:
            with open(filename, encoding="utf-8") as f:
                content = f.read()
        except (FileNotFoundError, IsADirectoryError):
            return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {filename}"}

    lines = content.splitlines()
    result_lines = []
    for line in lines:
        fields = line.split()
        try:
            if eval(program, {"NF": len(fields), "NR": len(result_lines) + 1,
                              "$0": line, **{f"${i+1}": f for i, f in enumerate(fields)}}):
                result_lines.append(line)
        except Exception:
            pass
    return {"exit_code": 0, "stdout": "\n".join(result_lines) + ("\n" if result_lines else ""), "stderr": ""}