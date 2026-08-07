"""Task management — create, list, claim, complete."""

from modules.sql.workspace import WorkspaceDB


def _scope(workspace_id: str):
    db = WorkspaceDB()
    return db, db.for_workspace(workspace_id)


def create(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    title = inputs.get("title", "")
    description = inputs.get("description", "")
    priority = int(inputs.get("priority", 0))
    parent_id = inputs.get("parent_id")
    difficulty = inputs.get("difficulty", "medium")
    role_required = inputs.get("role_required", "")
    team_id = int(inputs.get("team_id", -1))
    if not workspace_id or not title:
        return {"ok": False, "error": "workspace_id et title requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.create(title, description, priority, parent_id,
                                  difficulty=difficulty, role_required=role_required,
                                  team_id=team_id)
        db.close()
        return {"ok": True, "task": task}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_pending(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db, scope = _scope(workspace_id)
        tasks = scope.tasks.list_pending()
        db.close()
        return {"ok": True, "tasks": tasks, "count": len(tasks)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_all(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db, scope = _scope(workspace_id)
        tasks = scope.tasks.list_all()
        db.close()
        return {"ok": True, "tasks": tasks, "count": len(tasks)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id et task_id requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if task:
            files = scope.tasks.get_files(int(task_id))
            task["files"] = files
        db.close()
        if not task:
            return {"ok": False, "error": "tâche introuvable"}
        return {"ok": True, "task": task}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def claim(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    agent_name = inputs.get("agent_name", "")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id et task_id requis"}
    if not agent_name:
        return {"ok": False, "error": "agent_name requis"}
    try:
        db, scope = _scope(workspace_id)
        claimed = scope.tasks.claim(int(task_id), agent_name)
        db.close()
        if not claimed:
            return {"ok": False, "error": "tâche déjà prise ou introuvable"}
        return {"ok": True, "task_id": task_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def done(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    branch = inputs.get("branch", "")
    commit_hash = inputs.get("commit_hash", "")
    approve = bool(inputs.get("approve", False))
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id et task_id requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if task is None:
            db.close()
            return {"ok": False, "error": "tâche introuvable"}
        # Garde anti faux-positif : une tâche de CODAGE ne peut pas être
        # marquée done sans commit (le code produit est le livrable). Sans
        # ça, les membres greedy marquent des tâches done à vide et la
        # mission « avance » sans livrable réel.
        role = (task.get("role_required") or "").lower()
        if role.startswith("coder") and not (branch or commit_hash):
            db.close()
            return {"ok": False,
                    "error": "tâche de codage : commit_hash/branch requis "
                             "(travail non livré — commit d'abord via git)"}
        # Flux de review : une tâche de codage livrée passe en `review` (pas
        # directement done) — le reviewer la valide ensuite en `done` avec
        # approve=true. Sans ça, « une tâche n'est done que si reviewée » est
        # violée (le coder se marque done lui-même sans validation).
        if role.startswith("coder") and not approve:
            task = scope.tasks.set_status(int(task_id), "review",
                                          branch=branch,
                                          commit_hash=commit_hash)
            db.close()
            return {"ok": True, "task": task, "review": True,
                    "note": "tâche livrée en review — le reviewer doit la "
                            "valider (task_done approve=true)"}
        task = scope.tasks.set_status(int(task_id), "done",
                                      branch=branch,
                                      commit_hash=commit_hash)
        db.close()
        if not task:
            return {"ok": False, "error": "tâche introuvable"}
        return {"ok": True, "task": task}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def claim_next(inputs: dict, home: str) -> dict:
    """Pioche la prochaine tâche dispo pour le rôle de l'agent (greedy).

    La tâche au statut cible la plus prioritaire correspondant à role_required
    passe en 'running'. Par défaut pioche les 'pending' ; le reviewer passe
    status='review' pour piocher les tâches livrées à valider.
    """
    workspace_id = inputs.get("workspace_id", "")
    role_required = inputs.get("role_required", "")
    team_id = int(inputs.get("team_id", -1))
    status = inputs.get("status", "pending")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.claim_next(role_required=role_required,
                                      team_id=team_id, status=status)
        db.close()
        if not task:
            return {"ok": False, "error": "aucune tâche dispo pour ce rôle"}
        return {"ok": True, "task": task, "task_id": task["task_id"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def add_file(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    path = inputs.get("path", "")
    role = inputs.get("role", "source")
    if not workspace_id or task_id is None or not path:
        return {"ok": False, "error": "workspace_id, task_id et path requis"}
    try:
        db, scope = _scope(workspace_id)
        scope.tasks.add_file(int(task_id), path, role)
        db.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_files(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id et task_id requis"}
    try:
        db, scope = _scope(workspace_id)
        files = scope.tasks.get_files(int(task_id))
        db.close()
        return {"ok": True, "files": files}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_tasks(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    show_all = inputs.get("all", False)
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db, scope = _scope(workspace_id)
        tasks = scope.tasks.list_all() if show_all else scope.tasks.list_pending()
        db.close()
        return {"ok": True, "tasks": tasks, "count": len(tasks)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["create", "list_pending", "list_all", "list_tasks", "get",
              "claim", "claim_next", "done", "add_file", "get_files"]
