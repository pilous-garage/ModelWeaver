"""Chat room — post, read, threads."""

from modules.sql.workspace import WorkspaceDB


def post(inputs: dict, ws: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    content = inputs.get("content", "")
    msg_type = inputs.get("msg_type", "text")
    sender = inputs.get("sender_agent_id")
    parent_id = inputs.get("parent_id")
    if not workspace_id or not content:
        return {"ok": False, "error": "workspace_id et content requis"}
    try:
        db = WorkspaceDB()
        scope = db.for_workspace(workspace_id)
        msg = scope.chat.post(sender, content, msg_type, parent_id)
        db.close()
        return {"ok": True, "message": msg}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def recent(inputs: dict, ws: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    limit = int(inputs.get("limit", 50))
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db = WorkspaceDB()
        scope = db.for_workspace(workspace_id)
        messages = scope.chat.recent(limit)
        db.close()
        return {"ok": True, "messages": messages, "count": len(messages)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def thread(inputs: dict, ws: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    root_id = inputs.get("root_id")
    if not workspace_id or root_id is None:
        return {"ok": False, "error": "workspace_id et root_id requis"}
    try:
        db = WorkspaceDB()
        scope = db.for_workspace(workspace_id)
        messages = scope.chat.thread(int(root_id))
        db.close()
        return {"ok": True, "messages": messages, "count": len(messages)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["post", "recent", "thread"]
