"""sleep — pause d'exécution."""

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_sleep(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: sleep <seconds>"}
    try:
        seconds = float(args[0])
    except ValueError:
        return {"exit_code": 1, "stdout": "", "stderr": f"invalid number: {args[0]}"}
    time.sleep(seconds)
    return {"exit_code": 0, "stdout": "", "stderr": ""}