"""paste — fusion de fichiers colonne par colonne."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_paste(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    delimiter = "\t"
    filenames = args if args else []
    contents = []
    for fname in filenames:
        target = auth.check_path(Path(workdir) / fname)
        if not target.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {target}"}
        contents.append(target.read_text(encoding="utf-8").splitlines())
    if not contents and stdin:
        contents = [stdin.splitlines()]

    max_lines = max((len(c) for c in contents), default=0)
    result = []
    for i in range(max_lines):
        row = []
        for content in contents:
            row.append(content[i] if i < len(content) else "")
        result.append(delimiter.join(row))
    return {"exit_code": 0, "stdout": "\n".join(result) + ("\n" if result else ""), "stderr": ""}