"""wait_for — enregistre un agent en attente d'une condition (agents.db)."""

from typing import Any, Dict


def _agent_db():
    from modules.sql.db import AgentsDB
    return AgentsDB()


def registrer(inputs: dict, home: str) -> dict:
    """Enregistre l'agent en attente d'une condition.

    La condition est un dict JSON : {type, workspace_id, role, team_id}.
    Le waker la lira et réveillera l'agent quand elle est remplie (FIFO).
    """
    try:
        from modules.sql.db import AgentsDB
    except Exception as e:
        return {"ok": False, "error": f"import AgentsDB: {e}"}
    agent_id = inputs.get("agent_id")
    cond_type = inputs.get("type", "")
    workspace_id = inputs.get("workspace_id", "")
    if not agent_id or not cond_type or not workspace_id:
        return {"ok": False, "error": "agent_id, type et workspace_id requis"}
    condition = {
        "type": cond_type,
        "workspace_id": workspace_id,
        "role": inputs.get("role", ""),
        "team_id": int(inputs.get("team_id", -1)),
        "types": inputs.get("types") or [],
    }
    try:
        db = AgentsDB()
        wait_id = db.wait_for.register(int(agent_id), condition)
        db.close()
        return {"ok": True, "wait_id": wait_id, "waiting": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["registrer"]
