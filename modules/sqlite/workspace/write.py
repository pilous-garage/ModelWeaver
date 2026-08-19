from __future__ import annotations

from typing import Any, Dict, Optional

from modules.sqlite.workspace import db


def create_workspace(name: str, description: str = "") -> int:
    with db().in_write():
        db().table("workspaces").insert({"name": name, "description": description})
        return db()._conn.lastrowid


def create_task(workspace_id: int, title: str, description: str = "", status: str = "new") -> int:
    with db().in_write():
        db().table("tasks").insert({
            "workspace_id": workspace_id,
            "title": title,
            "description": description,
            "status": status,
        })
        return db()._conn.lastrowid


def update_task_status(task_id: int, status: str) -> None:
    with db().in_write():
        db().table("tasks").update(where={"task_id": task_id}, data={"status": status})


def create_sub_task(task_id: int, sub_type: str, status: str = "new") -> int:
    with db().in_write():
        db().table("sub_tasks").insert({
            "task_id": task_id,
            "sub_type": sub_type,
            "status": status,
        })
        return db()._conn.lastrowid


def update_sub_task_status(sub_task_id: int, status: str) -> None:
    with db().in_write():
        db().table("sub_tasks").update(where={"sub_task_id": sub_task_id}, data={"status": status})


def create_ask_new_task(workspace_id: int, agent_id: str, types: str, status: str = "pending") -> int:
    with db().in_write():
        db().table("ask_new_task").insert({
            "workspace_id": workspace_id,
            "agent_id": agent_id,
            "types": types,
            "status": status,
        })
        return db()._conn.lastrowid


def update_ask_new_task_status(ask_id: int, status: str) -> None:
    with db().in_write():
        db().table("ask_new_task").update(where={"ask_id": ask_id}, data={"status": status})


__all__ = [
    "create_workspace",
    "create_task",
    "update_task_status",
    "create_sub_task",
    "update_sub_task_status",
    "create_ask_new_task",
    "update_ask_new_task_status",
]
