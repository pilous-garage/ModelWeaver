"""pskill — tuer des processus avec autorisation agent/team."""

import signal
import os as _os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ..auth import ShellAuth


def cmd_pskill(
    args: List[str],
    stdin: Optional[str],
    workdir: str,
    auth: "ShellAuth",
    stdout_to: Optional[str],
    stderr_to: Optional[str],
) -> Dict[str, Any]:
    if not args:
        return {"exit_code": 1, "stdout": "", "stderr": "usage: pkill <pid> [...]"}
    killed = 0
    errors = []
    from ..tracker import tracker
    for pid_arg in args:
        try:
            pid = int(pid_arg)
        except ValueError:
            errors.append(f"pid invalide : {pid_arg}")
            continue
        proc_info = tracker.get(pid)
        if proc_info is None:
            try:
                _os.kill(pid, signal.SIGTERM)
                killed += 1
                continue
            except ProcessLookupError:
                errors.append(f"process introuvable : {pid}")
                continue
            except PermissionError:
                errors.append(f"permission refusée : {pid}")
                continue
        if auth.can_kill_process(pid, proc_info.get("agent_id"), proc_info.get("team_id")):
            try:
                _os.kill(pid, signal.SIGTERM)
                tracker.kill(pid)
                killed += 1
            except ProcessLookupError:
                tracker.kill(pid)
                errors.append(f"process déjà terminé : {pid}")
            except PermissionError:
                errors.append(f"permission refusée : {pid}")
        else:
            req_type = "live" if auth.role in ("leader", "owner") else "pending_user"
            req = auth.submit_authorization_request(
                action="pskill",
                target={
                    "pid": pid,
                    "target_agent_id": proc_info.get("agent_id"),
                    "target_team_id": proc_info.get("team_id"),
                    "cmd": proc_info.get("cmd"),
                },
                reason=f"agent {auth.agent_id} demande à tuer pid {pid} "
                       f"(agent={proc_info.get('agent_id')})",
                request_type=req_type,
            )
            if req:
                result = {
                    "status": "pending_approval",
                    "request_id": req.request_id,
                    "request_type": req_type,
                    "exit_code": 0,
                    "stdout": f"demande d'autorisation créée : {req.request_id}",
                    "stderr": "",
                }
                if req.continue_anyway:
                    result["continue_anyway"] = True
                    result["stdout"] = (
                        f"demande d'autorisation créée : {req.request_id} "
                        f"(en attente validation humaine, continue sans)"
                    )
                return result
            errors.append(
                f"interdit : pid {pid} (agent={proc_info.get('agent_id')}, "
                f"team={proc_info.get('team_id')})"
            )
    stdout_parts = []
    if killed:
        stdout_parts.append(f"{killed} processus terminé(s)")
    if errors:
        return {"exit_code": 1, "stdout": "; ".join(stdout_parts), "stderr": "; ".join(errors)}
    return {"exit_code": 0, "stdout": "; ".join(stdout_parts) if stdout_parts else "rien à terminer", "stderr": ""}