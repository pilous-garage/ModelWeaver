"""issue_link_workspace — lie une issue à son workspace d'analyse.

L'analyste crée les tasks de découpage d'une issue dans un workspace dédié
(parfois différent du workspace de l'issue). Ce skill enregistre le lien
issue.analysis_workspace_id → workspace, pour que le waker puisse marquer
l'issue 'done' quand toutes ses tasks sont terminées.
"""


def link(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    issue_id = inputs.get("issue_id")
    analysis_workspace = inputs.get("analysis_workspace", "")
    if not workspace_id or issue_id is None or not analysis_workspace:
        return {"ok": False,
                "error": "workspace_id, issue_id et analysis_workspace requis"}
    try:
        from modules.sql.workspace import WorkspaceDB
        db = WorkspaceDB()
        cur = db.conn.execute(
            "UPDATE issues SET analysis_workspace_id = ? "
            "WHERE issue_id = ? AND workspace_id = ?",
            (analysis_workspace, int(issue_id), workspace_id))
        db.conn.commit()
        db.close()
        if cur.rowcount == 0:
            return {"ok": False, "error": "issue introuvable"}
        return {"ok": True, "issue_id": int(issue_id),
                "analysis_workspace": analysis_workspace}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["link"]
