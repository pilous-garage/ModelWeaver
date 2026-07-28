"""echo — affichage."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_echo(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    text = " ".join(args)
    if stdin:
        text = f"{stdin}{' ' if text else ''}{text}" if text else stdin
    return {"exit_code": 0, "stdout": text + "\n", "stderr": ""}