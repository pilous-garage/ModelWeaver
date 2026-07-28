"""find — recherche de fichiers récursive."""

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_find(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        base = auth.check_path(Path(workdir))
    else:
        base = auth.check_path(Path(workdir) / args[0])
    if not base.exists():
        return {"exit_code": 1, "stdout": "", "stderr": f"chemin introuvable : {base}"}
    results = []
    for root, _dirs, files in os.walk(base):
        for f in sorted(files):
            full = Path(root) / f
            try:
                rel = full.relative_to(base)
            except ValueError:
                rel = full
            results.append(str(rel))
    return {"exit_code": 0, "stdout": "\n".join(results) + ("\n" if results else ""), "stderr": ""}