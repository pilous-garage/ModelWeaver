"""Journalisation d'audit.

Migrée depuis services/skill_manager.py (_exec_log).
"""

from services.audit import audit


def log(inputs: dict, ws: str) -> dict:
    level = inputs.get("level", "info")
    message = inputs.get("message", "")
    action = inputs.get("action", "skill.log")
    agent_id = inputs.get("agent_id", "")
    hook_type = inputs.get("hook_type", "")
    status = inputs.get("status", "")
    error = inputs.get("error", "")
    ok = level not in ("error", "critical")
    audit(action, service="skill", actor=str(agent_id), ok=ok,
          level=level, message=message, hook_type=hook_type,
          status=status, error=error)
    return {"ok": True}


__skills__ = ["log"]
