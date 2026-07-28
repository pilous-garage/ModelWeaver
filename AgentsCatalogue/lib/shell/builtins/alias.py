"""alias / unalias — gestion des alias de commandes."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_alias(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    aliases = getattr(auth, "_shell_aliases", {})
    if not args:
        lines = [f"alias {k}='{v}'" for k, v in sorted(aliases.items())]
        return {"exit_code": 0, "stdout": "\n".join(lines) + ("\n" if lines else ""), "stderr": ""}
    for arg in args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            value = value.strip("'\"")
            aliases[key] = value
        else:
            if arg in aliases:
                return {"exit_code": 0, "stdout": f"alias {arg}='{aliases[arg]}'\n", "stderr": ""}
            return {"exit_code": 1, "stdout": "", "stderr": f"alias {arg} introuvable"}
    return {"exit_code": 0, "stdout": "", "stderr": ""}


def cmd_unalias(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    aliases = getattr(auth, "_shell_aliases", {})
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: unalias <name> [...]"}
    for arg in args:
        aliases.pop(arg, None)
    return {"exit_code": 0, "stdout": "", "stderr": ""}