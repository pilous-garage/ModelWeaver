"""cd — changement de répertoire."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_cd(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    target = Path(args[0]) if args else Path.home()
    if not target.is_absolute():
        target = Path(workdir) / target
    try:
        resolved = auth.check_path(target)
    except Exception as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)}
    if not resolved.is_dir():
        return {"exit_code": 1, "stdout": "", "stderr": f"pas un répertoire : {resolved}"}
    return {"exit_code": 0, "stdout": str(resolved), "stderr": "", "_cd_to": str(resolved)}