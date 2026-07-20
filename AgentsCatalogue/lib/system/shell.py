"""Exécution de commandes shell dans le sandbox.

Migrée depuis services/skill_manager.py (_exec_run_shell).
"""

from services.sandbox import Sandbox, SandboxError


def run_shell(inputs: dict, ws: str) -> dict:
    cmd = inputs.get("command", "")
    try:
        stdout, stderr, rc = Sandbox().run(cmd, cwd=ws, timeout=30)
        return {"stdout": stdout, "stderr": stderr, "exit_code": rc}
    except SandboxError as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


__skills__ = ["run_shell"]
