"""exit — quitter le shell."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_exit(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    code = int(args[0]) if args else 0
    return {"exit_code": code, "stdout": "", "stderr": "", "_exit_code": code}