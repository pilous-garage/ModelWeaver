"""jobs, fg, bg — gestion des jobs en arrière-plan."""

import signal
import os as _os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_jobs(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    bg_jobs = getattr(auth, "_shell_bg_jobs", {})
    if not bg_jobs:
        return {"exit_code": 0, "stdout": "aucun job en cours\n", "stderr": ""}
    lines = []
    for jid in sorted(bg_jobs.keys()):
        j = bg_jobs[jid]
        lines.append(f"[{jid}]  {j.get('status', '?')}  {j.get('cmd', '')}")
    return {"exit_code": 0, "stdout": "\n".join(lines) + "\n", "stderr": ""}


def cmd_fg(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    bg_jobs = getattr(auth, "_shell_bg_jobs", {})
    if not bg_jobs:
        return {"exit_code": 1, "stdout": "", "stderr": "aucun job en cours"}
    if args:
        try:
            jid = int(args[0])
        except ValueError:
            return {"exit_code": 1, "stdout": "", "stderr": f"job id invalide : {args[0]}"}
    else:
        jid = max(bg_jobs.keys(), default=None)
    if jid not in bg_jobs:
        return {"exit_code": 1, "stdout": "", "stderr": f"job {jid} introuvable"}
    return {"exit_code": 0, "stdout": f"fg: job {jid}…\n", "stderr": "", "_fg_jid": jid}


def cmd_bg(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    bg_jobs = getattr(auth, "_shell_bg_jobs", {})
    if not bg_jobs:
        return {"exit_code": 1, "stdout": "", "stderr": "aucun job en cours"}
    if args:
        try:
            jid = int(args[0])
        except ValueError:
            return {"exit_code": 1, "stdout": "", "stderr": f"job id invalide : {args[0]}"}
    else:
        jid = max(bg_jobs.keys(), default=None)
    if jid not in bg_jobs:
        return {"exit_code": 1, "stdout": "", "stderr": f"job {jid} introuvable"}
    bg_jobs[jid]["status"] = "running"
    return {"exit_code": 0, "stdout": f"bg: job {jid} relancé\n", "stderr": ""}