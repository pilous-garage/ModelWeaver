"""issue_block — signale un choix humain bloquant (domaine dialogue_agent).

L'agent rencontre une décision qui nécessite l'humain (architecture, design,
priorité). Il enregistre la question dans human_choice (status='pending'),
liée à la task/sub_task bloquée quand il le sait.

L'humain répond via l'API human_choice/answer ; le watcher (P10) réveille
ensuite la sub_task (thaw → unattributed) — plus d'issues.
"""

import json
import uuid


def block(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    project_id = inputs.get("project_id", "") or workspace_id
    question = inputs.get("question", "")
    options = inputs.get("options", [])
    task_id = inputs.get("task_id")
    sub_task_id = inputs.get("sub_task_id")
    if not project_id or not question:
        return {"ok": False,
                "error": "workspace_id/project_id et question requis"}
    try:
        from modules.sqlite.dialogue_agent import db
        from modules.sqlite.dialogue_agent import write as W
        d = db()
        choice_id = W.ask_human(
            d, question, agent_id=0, project_id=project_id,
            task_id=int(task_id) if task_id else None,
            sub_task_id=int(sub_task_id) if sub_task_id else None,
            options_json=json.dumps(options) if options else None,
            choice_id=f"hc_{uuid.uuid4().hex[:12]}")
        d.close()
        return {"ok": True, "choice_id": choice_id, "pending": True,
                "task_id": task_id, "sub_task_id": sub_task_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["block"]