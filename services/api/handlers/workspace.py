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
        from modules.sqlite.workspace.workspace import WorkspaceDB
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
        from modules.sqlite.workspace.workspace import WorkspaceDB
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


# ── Tasks (greedy) : pont HTTP vers workspacedb (le panel moniteur les lit) ──

def _tasks_scope(workspace_id: str):
    from modules.sqlite.workspace.workspace import WorkspaceDB
    db = WorkspaceDB()
    return db, db.for_workspace(workspace_id)


def op_workspace_tasks_list(params):
    """Liste les tâches d'un workspace (pending par défaut, `all`=true pour tout)."""
    workspace_id = params.get("workspace_id", "")
    show_all = bool(params.get("all", False))
    if not workspace_id:
        return {"status": "error", "error": "workspace_id requis"}
    try:
        db, scope = _tasks_scope(workspace_id)
        tasks = scope.tasks.list_all() if show_all else scope.tasks.list_pending()
        db.close()
        return {"status": "ok", "workspace_id": workspace_id, "tasks": tasks, "count": len(tasks)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_workspace_tasks_get(params):
    """Détail d'une tâche (status, role_required, résultat, commit…)."""
    workspace_id = params.get("workspace_id", "")
    task_id = params.get("task_id")
    if not workspace_id or task_id is None:
        return {"status": "error", "error": "workspace_id et task_id requis"}
    try:
        db, scope = _tasks_scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        files = scope.tasks.get_attachments(int(task_id), kind="file")
        db.close()
        if not task:
            return {"status": "error", "error": f"tâche {task_id} introuvable"}
        task["files"] = files
        return {"status": "ok", "task": task}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_workspace_tasks_claim_next(params):
    """Pioche le prochain token todo pour les task_types donnés (greedy)."""
    workspace_id = params.get("workspace_id", "")
    task_types = params.get("task_types", "") or params.get("role_required", "")
    max_difficulty = params.get("max_difficulty", "")
    if isinstance(task_types, str) and task_types and not str(task_types).startswith("["):
        task_types = [task_types]
    if not workspace_id or not task_types:
        return {"status": "error", "error": "workspace_id + task_types requis"}
    try:
        db, scope = _tasks_scope(workspace_id)
        task = scope.tasks.claim_next(task_types, max_difficulty or {})
        db.close()
        if not task:
            return {"status": "ok", "task": None}
        return {"status": "ok", "task": task, "task_id": task.get("task_id")}
    except Exception as e:
        return {"status": "error", "error": str(e)}


register("workspace/tasks/list",      op_workspace_tasks_list)
register("workspace/tasks/get",       op_workspace_tasks_get)
register("workspace/tasks/claim_next", op_workspace_tasks_claim_next)
register("workspace/issues/add",  op_workspace_issues_add)
register("workspace/issues/list", op_workspace_issues_list)


# ── human_choice : choix humain requis (escalade, dialogue_agent) ─────────

def op_human_choice_list(params):
    """Liste les choix humains en attente.

    params :
      - status : filtre (default 'pending')
      - workspace_id : filtre optionnel (compat, = project_id)
    Retourne {status, choices: [{choice_id, project_id, task_id, sub_task_id,
    question, options, ...}]}
    """
    status = params.get("status", "pending")
    workspace_id = params.get("workspace_id", "")
    try:
        from modules.sqlite.dialogue_agent import db
        from modules.sqlite.dialogue_agent import read as R
        d = db()
        choices = [c for c in R.human_choices(d, status)
                   if not workspace_id or c.get("project_id") == workspace_id]
        d.close()
        out = []
        for c in choices:
            c = dict(c)
            try:
                import json
                c["options"] = (json.loads(c.get("options_json"))
                                if c.get("options_json") else [])
            except Exception:
                c["options"] = []
            out.append(c)
        return {"status": "ok", "choices": out, "count": len(out)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_human_choice_answer(params):
    """Répond à un choix humain → le watcher (P10) réveille la sub_task
    bloquée (thaw → unattributed).

    params :
      - choice_id : l'id du choix (human_choice/ask)
      - response  : la réponse de l'humain (texte libre ou option choisie)
    """
    choice_id = params.get("choice_id", "")
    response = params.get("response", "")
    if not choice_id or not response:
        return {"status": "error", "error": "choice_id et response requis"}
    try:
        from modules.sqlite.dialogue_agent import db
        from modules.sqlite.dialogue_agent import read as R
        from modules.sqlite.dialogue_agent import write as W
        d = db()
        row = R.human_choice_get(d, choice_id)
        if not row:
            d.close()
            return {"status": "error", "error": "choice_id introuvable"}
        if row["status"] == "answered":
            d.close()
            return {"status": "ok", "note": "déjà répondu", "choice_id": choice_id}
        W.answer_human(d, choice_id, response)
        d.close()
        return {"status": "ok", "choice_id": choice_id, "answered": True}
    except Exception as e:
        return {"status": "error", "error": str(e)}


register("human_choice/list",   op_human_choice_list)
register("human_choice/answer", op_human_choice_answer)
