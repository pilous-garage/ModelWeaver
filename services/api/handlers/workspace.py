"""Routes workspace — issues en masse (seed depuis issues.md, tests, etc.)."""

from services.api.router import register


def op_workspace_issues_add(params):
    """Ajoute une liste d'issues à un workspace.

    params :
      - workspace_id : workspace cible (ex. mw-swarm)
      - issues : liste de {title, description, priority?, team_id?}
      - team_id : team_id par défaut si non précisé par issue (-1 = projet)
    Retourne {status, added, issues: [{issue_id, title}]}.
    """
    workspace_id = params.get("workspace_id", "")
    issues = params.get("issues", [])
    default_team_id = int(params.get("team_id", -1))
    if not workspace_id:
        return {"status": "error", "error": "workspace_id requis"}
    if not isinstance(issues, list) or not issues:
        return {"status": "error", "error": "issues requis (liste non vide)"}

    try:
        from modules.sql.workspace import WorkspaceDB
        wdb = WorkspaceDB()
        added = []
        for it in issues:
            title = (it.get("title") or "").strip()
            if not title:
                continue
            desc = it.get("description", "") or ""
            prio = int(it.get("priority", 0))
            team = int(it.get("team_id", default_team_id))
            scope = wdb.for_workspace(workspace_id)
            issue = scope.issues.create(title, desc, prio, team_id=team)
            added.append({"issue_id": issue["issue_id"], "title": title})
        wdb.close()
        return {"status": "ok", "added": len(added), "issues": added}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_workspace_issues_list(params):
    """Liste les issues d'un workspace (option filtre par status)."""
    workspace_id = params.get("workspace_id", "")
    status = params.get("status", "")
    if not workspace_id:
        return {"status": "error", "error": "workspace_id requis"}
    try:
        from modules.sql.workspace import WorkspaceDB
        wdb = WorkspaceDB()
        if status:
            rows = wdb.conn.execute(
                "SELECT * FROM issues WHERE workspace_id = ? AND status = ? "
                "ORDER BY priority DESC, created_at",
                (workspace_id, status)).fetchall()
        else:
            rows = wdb.conn.execute(
                "SELECT * FROM issues WHERE workspace_id = ? "
                "ORDER BY priority DESC, created_at",
                (workspace_id,)).fetchall()
        wdb.close()
        return {"status": "ok", "issues": [dict(r) for r in rows],
                "count": len(rows)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


register("workspace/issues/add",  op_workspace_issues_add)
register("workspace/issues/list", op_workspace_issues_list)


# ── human_choice : choix humain requis (issues bloquées) ─────────────

def op_human_choice_list(params):
    """Liste les choix humains en attente (issues bloquées).

    params :
      - status : filtre (default 'pending')
      - workspace_id : filtre optionnel
    Retourne {status, choices: [{choice_id, issue_id, question, options, ...}]}
    """
    status = params.get("status", "pending")
    workspace_id = params.get("workspace_id", "")
    try:
        from modules.sql.workspace import WorkspaceDB
        wdb = WorkspaceDB()
        if workspace_id:
            rows = wdb.conn.execute(
                "SELECT h.* FROM human_choice h "
                "JOIN issues i ON i.issue_id = h.issue_id "
                "WHERE h.status = ? AND i.workspace_id = ? "
                "ORDER BY h.asked_at", (status, workspace_id)).fetchall()
        else:
            rows = wdb.conn.execute(
                "SELECT * FROM human_choice WHERE status = ? "
                "ORDER BY asked_at", (status,)).fetchall()
        wdb.close()
        out = []
        for r in rows:
            d = dict(r)
            try:
                import json
                d["options"] = json.loads(d.get("options_json")) if d.get("options_json") else []
            except Exception:
                d["options"] = []
            out.append(d)
        return {"status": "ok", "choices": out, "count": len(out)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_human_choice_answer(params):
    """Répond à un choix humain → débloque l'issue (le watcher la remet open).

    params :
      - choice_id : l'id du choix (human_choice/ask)
      - response  : la réponse de l'humain (texte libre ou option choisie)
    """
    choice_id = params.get("choice_id", "")
    response = params.get("response", "")
    if not choice_id or not response:
        return {"status": "error", "error": "choice_id et response requis"}
    try:
        from modules.sql.workspace import WorkspaceDB
        wdb = WorkspaceDB()
        row = wdb.conn.execute(
            "SELECT * FROM human_choice WHERE choice_id = ?",
            (choice_id,)).fetchone()
        if not row:
            wdb.close()
            return {"status": "error", "error": "choice_id introuvable"}
        if row["status"] == "answered":
            wdb.close()
            return {"status": "ok", "note": "déjà répondu", "choice_id": choice_id}
        wdb.conn.execute(
            "UPDATE human_choice SET status='answered', response=?, "
            "answered_at=strftime('%s','now') WHERE choice_id = ?",
            (response, choice_id))
        # L'issue reste 'blocked' ; le watcher la débloquera au prochain cycle
        # (injecte la réponse + remet open).
        wdb.conn.commit()
        wdb.close()
        return {"status": "ok", "choice_id": choice_id, "answered": True}
    except Exception as e:
        return {"status": "error", "error": str(e)}


register("human_choice/list",   op_human_choice_list)
register("human_choice/answer", op_human_choice_answer)
