"""Skill shell — expose l'AgentShell interne aux workflows YAML.

Usage :
    type: call
    fn: shell/exec@v1
    inputs:
      command: "ls -la"
"""

from pathlib import Path


def exec(inputs: dict, home: str) -> dict:
    cmd = inputs.get("command", "")
    if not cmd:
        return {"stdout": "", "stderr": "command empty", "exit_code": 1, "status": "error"}

    agent_id = Path(home).name

    try:
        from services.agent_shell_manager import agent_shell_manager
        agent_shell_manager.init()
        ag_sh = agent_shell_manager.get(agent_id)
        if ag_sh is None:
            return {"stdout": "", "stderr": f"shell non trouvé pour {agent_id}", "exit_code": 1, "status": "error"}
        result = ag_sh.run(cmd)
        return {
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "exit_code": result.get("exit_code", 1),
            "status": result.get("status", "error"),
            "request_id": result.get("request_id"),
            "continue_anyway": result.get("continue_anyway", False),
        }
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1, "status": "error"}


__skills__ = ["exec"]