"""uniq — lignes uniques consécutives."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_uniq(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    content: str = stdin or ""
    filenames: List[str] = []
    i = 0
    while i < len(args):
        if args[i].startswith("-"):
            i += 1
        else:
            filenames.append(args[i])
            i += 1

    if filenames:
        for fname in filenames:
            target = auth.check_path(Path(workdir) / fname)
            if not target.exists():
                return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {target}"}
            content = target.read_text(encoding="utf-8")
    lines = content.splitlines()
    seen: Optional[str] = None
    result = []
    for line in lines:
        if line != seen:
            result.append(line)
            seen = line
    return {"exit_code": 0, "stdout": "\n".join(result) + ("\n" if result else ""), "stderr": ""}