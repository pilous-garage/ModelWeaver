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
