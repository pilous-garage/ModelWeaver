"""read — lectures thin du domaine task.

CRUD pur sur entry_request / tasks / task_dependencies / ask_new_task ;
la logique (sélection de work par l'agent greedy) vit dans task.py — voir la
règle dans base.py."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db


def list_entry_requests(db: Db, workspace_id: str = "") -> List[Dict[str, Any]]:
    tbl = db.table("entry_request")
    if workspace_id:
        return tbl.select(where={"workspace_id": workspace_id},
                          order_by="asked_at DESC")
    return tbl.select(order_by="asked_at DESC")


def get_entry_request(db: Db, entry_request_id: int) -> Optional[Dict[str, Any]]:
    return db.table("entry_request").get({"entry_request_id": entry_request_id})


def list_tasks(db: Db, workspace_id: str = "", status: str = "",
               assigned_to: str = "") -> List[Dict[str, Any]]:
    tbl = db.table("tasks")
    w: Dict[str, Any] = {}
    if workspace_id:
        w["workspace_id"] = workspace_id
    if assigned_to:
        w["assigned_to"] = assigned_to
    if status:
        w["status"] = status
    return tbl.select(where=w or None,
                      order_by="workspace_id, priority DESC, created_at")


def get_task(db: Db, task_id: int) -> Optional[Dict[str, Any]]:
    return db.table("tasks").get({"task_id": task_id})


def list_sub_tasks(db: Db, parent_task_id: int) -> List[Dict[str, Any]]:
    return db.table("tasks").select(
        where={"parent_task_id": parent_task_id},
        order_by="priority DESC, created_at")


def children_of(db: Db, task_id: int, status: str = "") -> List[Dict[str, Any]]:
    w: Dict[str, Any] = {"parent_task_id": task_id}
    if status:
        w["status"] = status
    return db.table("tasks").select(where=w, order_by="priority DESC, created_at")


def list_dependencies(db: Db, task_id: int) -> List[Dict[str, Any]]:
    return db.table("task_dependencies").select(
        where={"child_id": task_id}, order_by="parent_id")


def list_waiting_for(db: Db, parent_task_id: int) -> List[Dict[str, Any]]:
    """Sub_tasks en waiting_deps dont un parent = task_id est satisfait."""
    return db.sql(
        "SELECT d.child_id FROM task_dependencies d "
        "JOIN tasks p ON p.task_id = d.parent_id "
        "WHERE d.parent_id = ? AND p.status = d.required_state "
        "AND EXISTS (SELECT 1 FROM tasks c WHERE c.task_id = d.child_id AND "
        "c.status='waiting_deps')", (parent_task_id,))


def pending_asks(db: Db, workspace_id: str = "") -> List[Dict[str, Any]]:
    tbl = db.table("ask_new_task")
    w: Dict[str, Any] = {"status": "pending"}
    if workspace_id:
        w["workspace_id"] = workspace_id
    return tbl.select(where=w, order_by="requested_at")


def list_versions_task(db: Db, entry_request_id: int) -> List[Dict[str, Any]]:
    """Historique des tâches d'une conversation (par entry_request_id)."""
    return db.table("tasks").select(
        where={"entry_request_id": entry_request_id},
        cols=["task_id", "parent_task_id", "sub_task_type", "status",
              "tag", "created_at"],
        order_by="created_at")
