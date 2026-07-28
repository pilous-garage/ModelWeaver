"""export — définir une variable d'environnement du shell."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_export(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not hasattr(auth, "_shell_env") or auth._shell_env is None:
        auth._shell_env = {}
    env = auth._shell_env
    if not args:
        lines = []
        for key in sorted(env.keys()):
            lines.append(f"export {key}={env[key]}")
        return {"exit_code": 0, "stdout": "\n".join(lines) + ("\n" if lines else ""), "stderr": ""}
    for arg in args:
        if "=" in arg:
            key, value = arg.split("=", 1)
            env[key] = value
        else:
            return {"exit_code": 1, "stdout": "", "stderr": f"format invalide : {arg} (attendu KEY=VALUE)"}
    return {"exit_code": 0, "stdout": "", "stderr": ""}