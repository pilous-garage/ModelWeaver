from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.workspace import db


def get_workspace(workspace_id: int) -> Optional[Dict[str, Any]]:
    rows = db().table("workspaces").select(where={"workspace_id": workspace_id})
    return rows[0] if rows else None


def list_tasks(workspace_id: int, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {"workspace_id": workspace_id}
    if status:
        where["status"] = status
    return db().table("tasks").select(where=where, limit=limit, order_by="created_at DESC")


def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    rows = db().table("tasks").select(where={"task_id": task_id})
    return rows[0] if rows else None


def list_sub_tasks(task_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {"task_id": task_id}
    if status:
        where["status"] = status
    return db().table("sub_tasks").select(where=where, order_by="created_at DESC")


def get_sub_task(sub_task_id: int) -> Optional[Dict[str, Any]]:
    rows = db().table("sub_tasks").select(where={"sub_task_id": sub_task_id})
    return rows[0] if rows else None


def list_ask_new_task(workspace_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {"workspace_id": workspace_id}
    if status:
        where["status"] = status
    return db().table("ask_new_task").select(where=where, order_by="created_at DESC")


__all__ = [
    "get_workspace",
    "list_tasks",
    "get_task",
    "list_sub_tasks",
    "get_sub_task",
    "list_ask_new_task",
]
