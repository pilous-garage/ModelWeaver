"""tr — traduction de caractères."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_tr(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if len(args) < 2:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: tr SET1 SET2"}
    set1 = args[0]
    set2 = args[1]
    content = stdin or ""
    if len(set1) == 1 and len(set2) == 1:
        result = content.replace(set1, set2)
    else:
        table = str.maketrans(set1, set2)
        result = content.translate(table)
    return {"exit_code": 0, "stdout": result, "stderr": ""}