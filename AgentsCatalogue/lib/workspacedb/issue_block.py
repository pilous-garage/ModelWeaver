"""issue_block — signale un choix humain bloquant sur une issue.

L'agent rencontre une décision qui nécessite l'humain (architecture, design,
priorité). Il bloque l'issue (status='blocked' — le swarm ne la repioche pas)
et enregistre la question dans human_choice (status='pending').

L'humain répond via l'API human_choice/answer ; le watcher débloque ensuite
l'issue (blocked + answered → open + réponse injectée).
"""

import uuid


def block(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    issue_id = inputs.get("issue_id")
    question = inputs.get("question", "")
    options = inputs.get("options", [])
    if not workspace_id or issue_id is None or not question:
        return {"ok": False,
                "error": "workspace_id, issue_id et question requis"}
    try:
        from modules.sql.workspace import WorkspaceDB
        db = WorkspaceDB()
        import json
        choice_id = f"hc_{uuid.uuid4().hex[:12]}"
        db.conn.execute(
            "INSERT INTO human_choice (choice_id, issue_id, question, "
            "options_json, status) VALUES (?, ?, ?, ?, 'pending')",
            (choice_id, int(issue_id), question,
             json.dumps(options) if options else None))
        # bloquer l'issue : le swarm ne la repioche pas tant qu'elle est
        # en attente d'une décision humaine.
        db.conn.execute(
            "UPDATE issues SET status = 'blocked', updated_at = datetime('now') "
            "WHERE issue_id = ? AND workspace_id = ?",
            (int(issue_id), workspace_id))
        db.conn.commit()
        db.close()
        return {"ok": True, "choice_id": choice_id, "blocked": True,
                "issue_id": int(issue_id)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["block"]
