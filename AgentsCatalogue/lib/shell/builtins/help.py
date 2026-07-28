"""help — liste des commandes disponibles."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_help(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    from ..builtins import _BUILTIN_MODULES as BUILTIN_MODULES
    command_names = sorted(BUILTIN_MODULES.keys())
    lines = ["Commandes built-in:"]
    for name in command_names:
        lines.append(f"  {name}")
    lines.append("")
    lines.append("Commandes système (lib_système):")
    from ..lib_système import available_commands
    for name, _path in available_commands():
        lines.append(f"  {name} (fallback)")
    return {"exit_code": 0, "stdout": "\n".join(lines) + "\n", "stderr": ""}