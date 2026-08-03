"""Issues d'un workspace — pioche greedy par l'analyste, mise à jour."""

from typing import Any, Dict

from AgentsCatalogue.lib.workspacedb.task import _scope


def create(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    title = inputs.get("title", "")
    description = inputs.get("description", "")
    priority = int(inputs.get("priority", 0))
    team_id = int(inputs.get("team_id", -1))
    if not workspace_id or not title:
        return {"ok": False, "error": "workspace_id et title requis"}
    try:
        db, scope = _scope(workspace_id)
        issue = scope.issues.create(title, description, priority, team_id=team_id)
        db.close()
        return {"ok": True, "issue": issue, "issue_id": issue["issue_id"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def claim_next(inputs: dict, home: str) -> dict:
    """Pioche la prochaine issue 'open' (greedy) → status 'analysing'."""
    workspace_id = inputs.get("workspace_id", "")
    agent_name = inputs.get("agent_name", "")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db, scope = _scope(workspace_id)
        row = scope.conn.execute(
            "SELECT * FROM issues WHERE workspace_id = ? AND status = 'open' "
            "ORDER BY priority DESC, created_at LIMIT 1",
            (workspace_id,)).fetchone()
        if not row:
            db.close()
            return {"ok": False, "error": "aucune issue dispo"}
        claimed = scope.issues.claim(row["issue_id"], agent_name)
        issue = scope.issues.get(row["issue_id"])
        db.close()
        if not claimed:
            return {"ok": False, "error": "issue déjà prise"}
        return {"ok": True, "issue": issue, "issue_id": issue["issue_id"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def update(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    issue_id = inputs.get("issue_id")
    if not workspace_id or issue_id is None:
        return {"ok": False, "error": "workspace_id et issue_id requis"}
    fields = {}
    for k in ("status", "title", "description", "priority", "assigned_to"):
        if inputs.get(k) is not None:
            fields[k] = inputs.get(k)
    if not fields:
        return {"ok": False, "error": "aucun champ à mettre à jour"}
    try:
        db, scope = _scope(workspace_id)
        issue = scope.issues.update(int(issue_id), **fields)
        db.close()
        return {"ok": True, "issue": issue}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    issue_id = inputs.get("issue_id")
    if not workspace_id or issue_id is None:
        return {"ok": False, "error": "workspace_id et issue_id requis"}
    try:
        db, scope = _scope(workspace_id)
        issue = scope.issues.get(int(issue_id))
        db.close()
        if not issue:
            return {"ok": False, "error": "issue introuvable"}
        return {"ok": True, "issue": issue}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["create", "claim_next", "update", "get"]
