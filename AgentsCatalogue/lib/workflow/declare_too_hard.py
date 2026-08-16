"""declare_too_hard — l'agent déclare que le travail courant est trop difficile.

Si sub_task_id est fourni : passe la sub_task doing → too_hard (le supervisor
décidera : bump + re-attribution, ou re-découpe). Sinon : écrit un signal dans
task_reports (la boucle FSM / supervisor pourra le lire). Mécanique, pas de LLM.
"""


def exec(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    sub_task_id = inputs.get("sub_task_id")
    task_id = inputs.get("task_id")
    reason = (inputs.get("reason") or "").strip() or "trop difficile (déclaré)"
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    if sub_task_id is not None:
        # sub_task → too_hard (le supervisor gère bump/découpe)
        try:
            from AgentsCatalogue.lib.workspacedb.taskflow import sub_task_too_hard
            return sub_task_too_hard({
                "workspace_id": workspace_id,
                "sub_task_id": sub_task_id,
                "reason": reason,
            }, home)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
    # Sans sub_task : signal dans task_reports (trace).
    if task_id is None:
        return {"ok": False, "error": "task_id ou sub_task_id requis"}
    try:
        from modules.sql.workspace import WorkspaceDB
        db = WorkspaceDB()
        sc = db.for_workspace(workspace_id)
        sc.tasks.add_report(int(task_id), "too_hard", reason)
        db.close()
        return {"ok": True, "too_hard": True, "reason": reason}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


__skills__ = ["exec"]
