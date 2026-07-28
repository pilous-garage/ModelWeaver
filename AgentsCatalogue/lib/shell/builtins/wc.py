"""wc — compteur de lignes, mots, caractères."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_wc(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    count_lines = True
    count_words = True
    count_chars = True
    filenames: List[str] = []

    i = 0
    while i < len(args):
        a = args[i]
        if a == "-l":
            count_lines = True
            count_words = False
            count_chars = False
            i += 1
        elif a == "-w":
            count_lines = False
            count_words = True
            count_chars = False
            i += 1
        elif a == "-c":
            count_lines = False
            count_words = False
            count_chars = True
            i += 1
        elif a.startswith("-"):
            i += 1
        else:
            filenames.append(a)
            i += 1

    def count_text(text: str) -> Tuple[int, int, int]:
        lines = text.splitlines()
        return len(lines), len(text.split()), len(text)

    if not filenames:
        content = stdin or ""
        nl, nw, nc = count_text(content)
        return {"exit_code": 0, "stdout": f"{nl:>8}{nw:>8}{nc:>8}\n", "stderr": ""}

    total_nl = total_nw = total_nc = 0
    output_lines = []
    for fname in filenames:
        target = auth.check_path(Path(workdir) / fname)
        if not target.exists():
            return {"exit_code": 1, "stdout": "", "stderr": f"fichier introuvable : {target}"}
        content = target.read_text(encoding="utf-8")
        nl, nw, nc = count_text(content)
        total_nl += nl
        total_nw += nw
        total_nc += nc
        output_lines.append(f"{nl:>8}{nw:>8}{nc:>8} {fname}")

    if len(filenames) > 1:
        output_lines.append(f"{total_nl:>8}{total_nw:>8}{total_nc:>8} total")

    return {"exit_code": 0, "stdout": "\n".join(output_lines) + "\n", "stderr": ""}