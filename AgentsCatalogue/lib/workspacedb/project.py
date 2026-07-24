"""Workspace project management — create, list, configure."""

from modules.sql.workspace import WorkspaceDB


def create(inputs: dict, ws: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    name = inputs.get("name", "")
    description = inputs.get("description", "")
    director = inputs.get("director")
    if not workspace_id or not name:
        return {"ok": False, "error": "workspace_id et name requis"}
    try:
        db = WorkspaceDB()
        db.workspaces.create(workspace_id, name, description, director)
        db.close()
        # Init shared git repo
        from services._common import mw_home
        shared = mw_home() / "repos" / f"{workspace_id}.git"
        shared.parent.mkdir(parents=True, exist_ok=True)
        from services.sandbox import Sandbox
        Sandbox().run(["git", "init", "--bare", str(shared)], shell=False, timeout=30)
        # Clone for director if specified
        if director:
            clone = mw_home() / "memagent" / str(director) / "workspace" / workspace_id
            if not clone.exists():
                clone.parent.mkdir(parents=True, exist_ok=True)
                Sandbox().run(["git", "clone", str(shared), str(clone)],
                              shell=False, timeout=30)
                Sandbox().run(
                    ["git", "-C", str(clone), "config", "user.email",
                     f"{director}@modelweaver.local"],
                    shell=False)
                Sandbox().run(
                    ["git", "-C", str(clone), "config", "user.name",
                     f"agent-{director}"],
                    shell=False)
        return {"ok": True, "workspace_id": workspace_id, "git_shared": str(shared)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_workspaces(inputs: dict, ws: str) -> dict:
    try:
        db = WorkspaceDB()
        workspaces = db.workspaces.list()
        db.close()
        return {"workspaces": workspaces, "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get(inputs: dict, ws: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db = WorkspaceDB()
        w = db.workspaces.get(workspace_id)
        db.close()
        if not w:
            return {"ok": False, "error": "workspace introuvable"}
        return {"ok": True, "workspace": w}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def config_get(inputs: dict, ws: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    key = inputs.get("key", "")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db = WorkspaceDB()
        if key:
            val = db.config.get(workspace_id, key)
            db.close()
            return {"ok": True, "key": key, "value": val}
        else:
            all_cfg = db.config.all(workspace_id)
            db.close()
            return {"ok": True, "config": all_cfg}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def config_set(inputs: dict, ws: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    key = inputs.get("key", "")
    value = inputs.get("value", "")
    if not workspace_id or not key:
        return {"ok": False, "error": "workspace_id et key requis"}
    try:
        db = WorkspaceDB()
        db.config.set(workspace_id, key, value)
        db.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def touch(inputs: dict, ws: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db = WorkspaceDB()
        db.workspaces.touch(workspace_id)
        db.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["create", "list_workspaces", "get", "config_get", "config_set", "touch"]
