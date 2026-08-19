"""Chat room — post, read, threads (domaine dialogue_agent).

Convention : project_id = la clé du dialogue (workspace_id accepté pour
compat — les agents le portent dans variables_json). Les dialogues
humain↔agent passent dans la MÊME arborescence (agent_id négatif).
"""

from modules.sqlite.dialogue_agent import db
from modules.sqlite.dialogue_agent import read as R
from modules.sqlite.dialogue_agent import write as W


def _project_id(workspace_id: str, project_id: str) -> str:
    return project_id or workspace_id


def post(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    project_id = inputs.get("project_id", "")
    content = inputs.get("content", "")
    msg_type = inputs.get("msg_type", "text")
    sender = inputs.get("sender_agent_id")
    conv_id = inputs.get("conv_id")
    attachments = inputs.get("attachments") or []
    pid = _project_id(workspace_id, project_id)
    if not pid or not content:
        return {"ok": False, "error": "workspace_id/project_id et content requis"}
    try:
        d = db()
        if not conv_id:
            conv_id = W.get_or_create_root_conversation(d, pid)
        mid = W.post_message(d, int(conv_id), int(sender) if sender else 0,
                             content, msg_type=msg_type, project_id=pid,
                             attachments=attachments)
        d.close()
        return {"ok": True, "message": {"msg_id": mid, "conv_id": conv_id}}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def recent(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    project_id = inputs.get("project_id", "")
    limit = int(inputs.get("limit", 50))
    conv_id = inputs.get("conv_id")
    pid = _project_id(workspace_id, project_id)
    if not pid:
        return {"ok": False, "error": "workspace_id/project_id requis"}
    try:
        d = db()
        if not conv_id:
            conv_id = W.get_or_create_root_conversation(d, pid)
        messages = R.messages(d, int(conv_id), limit)
        d.close()
        return {"ok": True, "messages": messages, "count": len(messages)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def thread(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    project_id = inputs.get("project_id", "")
    conv_id = inputs.get("conv_id") or inputs.get("root_id")
    pid = _project_id(workspace_id, project_id)
    if not pid or not conv_id:
        return {"ok": False, "error": "workspace_id/project_id et conv_id requis"}
    try:
        d = db()
        messages = R.message_thread(d, int(conv_id))
        d.close()
        return {"ok": True, "messages": messages, "count": len(messages)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["post", "recent", "thread"]