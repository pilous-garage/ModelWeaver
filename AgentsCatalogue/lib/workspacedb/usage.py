"""File usage tracking — touch, hot files for context."""

from modules.sql.workspace import WorkspaceDB


def touch_read(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    path = inputs.get("path", "")
    if not workspace_id or not path:
        return {"ok": False, "error": "workspace_id et path requis"}
    try:
        db = WorkspaceDB()
        scope = db.for_workspace(workspace_id)
        scope.usage.touch_read(path)
        db.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def touch_write(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    path = inputs.get("path", "")
    if not workspace_id or not path:
        return {"ok": False, "error": "workspace_id et path requis"}
    try:
        db = WorkspaceDB()
        scope = db.for_workspace(workspace_id)
        scope.usage.touch_write(path)
        db.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def hot(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    limit = int(inputs.get("limit", 10))
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db = WorkspaceDB()
        scope = db.for_workspace(workspace_id)
        files = scope.usage.hot(limit)
        db.close()
        return {"ok": True, "files": files}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def touch(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    path = inputs.get("path", "")
    mode = inputs.get("mode", "read")
    if not workspace_id or not path:
        return {"ok": False, "error": "workspace_id et path requis"}
    try:
        db = WorkspaceDB()
        scope = db.for_workspace(workspace_id)
        if mode == "write":
            scope.usage.touch_write(path)
        else:
            scope.usage.touch_read(path)
        db.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["touch_read", "touch_write", "touch", "hot"]
