"""Task management — create, list, claim, complete."""

from modules.sql.workspace import WorkspaceDB


def _scope(workspace_id: str):
    db = WorkspaceDB()
    return db, db.for_workspace(workspace_id)


def create(inputs: dict, ws: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    title = inputs.get("title", "")
    description = inputs.get("description", "")
    priority = int(inputs.get("priority", 0))
    parent_id = inputs.get("parent_id")
    if not workspace_id or not title:
        return {"ok": False, "error": "workspace_id et title requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.create(title, description, priority, parent_id)
        db.close()
        return {"ok": True, "task": task}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_pending(inputs: dict, ws: str) -> dict:
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


def list_all(inputs: dict, ws: str) -> dict:
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


def get(inputs: dict, ws: str) -> dict:
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


def claim(inputs: dict, ws: str) -> dict:
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


def done(inputs: dict, ws: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    branch = inputs.get("branch", "")
    commit_hash = inputs.get("commit_hash", "")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id et task_id requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.done(int(task_id), branch, commit_hash)
        db.close()
        if not task:
            return {"ok": False, "error": "tâche introuvable"}
        return {"ok": True, "task": task}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def add_file(inputs: dict, ws: str) -> dict:
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


def get_files(inputs: dict, ws: str) -> dict:
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


def list_tasks(inputs: dict, ws: str) -> dict:
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
              "claim", "done", "add_file", "get_files"]
