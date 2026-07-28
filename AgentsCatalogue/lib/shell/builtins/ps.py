"""ps — liste des processus actifs suivis."""

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_ps(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    from ..tracker import tracker
    procs = [p for p in tracker.all() if p.get("alive")]
    if not procs:
        return {"exit_code": 0, "stdout": "aucun processus actif\n", "stderr": ""}
    lines = ["PID\tAGENT\tTEAM\tCMD\tDURATION"]
    now = time.time()
    for p in procs:
        dur = int(now - p.get("launched_at", now))
        lines.append(f"{p['pid']}\t{p.get('agent_id','')}\t{p.get('team_id','')}\t{p.get('cmd','')}\t{dur}s")
    return {"exit_code": 0, "stdout": "\n".join(lines) + "\n", "stderr": ""}