"""date — affichage de la date courante."""

import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_date(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    fmt = args[0] if args else "%Y-%m-%d %H:%M:%S"
    return {"exit_code": 0, "stdout": f"{datetime.datetime.now().strftime(fmt)}\n", "stderr": ""}