"""read — lectures thin sur les tables infra du domaine local.

CRUD pur sur les tables fixes ; la logique (resolve_path, privileges matching,
buffer consumer) vit dans local.py — voir la règle dans base.py."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db


def get_meta(db: Db, key: str) -> Optional[Any]:
    r = db.table("global_local_meta").get({"key": key}, cols=["value"])
    return r["value"] if r else None


def list_data_types(db: Db) -> List[Dict[str, Any]]:
    return db.table("global_local_data_type").select(
        cols=["data_type_id", "code", "description", "active", "created_at"],
        order_by="code")


def get_data_type(db: Db, code: str) -> Optional[Dict[str, Any]]:
    return db.table("global_local_data_type").get(
        {"code": code},
        cols=["data_type_id", "code", "description", "active", "created_at"])


def get_namespace(db: Db, ns: str) -> Optional[Dict[str, Any]]:
    return db.table("global_local_namespace").get({"ns": ns})


def list_namespaces(db: Db, parent: Optional[str] = None) -> List[Dict[str, Any]]:
    tbl = db.table("global_local_namespace")
    if parent is None:
        return tbl.select(order_by="ns")
    return tbl.select(where={"parent": parent}, order_by="ns")


def list_namespaces_recursive(db: Db, prefix: str = "") -> List[Dict[str, Any]]:
    tbl = db.table("global_local_namespace")
    if not prefix:
        return tbl.select(order_by="ns")
    return tbl.sql(
        "SELECT * FROM global_local_namespace WHERE ns = ? OR ns LIKE ? ORDER BY ns",
        (prefix, prefix + "/%"))


def get_path(db: Db, path_name: str) -> Optional[Dict[str, Any]]:
    return db.table("global_local_path").get({"path_name": path_name})


def list_paths(db: Db, scheme: str = "") -> List[Dict[str, Any]]:
    tbl = db.table("global_local_path")
    if scheme:
        return tbl.select(where={"scheme": scheme}, order_by="path_name")
    return tbl.select(order_by="path_name")


def get_privilege(db: Db, id_auth: int) -> Optional[Dict[str, Any]]:
    return db.table("global_local_privilege").get({"id_auth": id_auth})


def list_privileges(db: Db, kind: str = "") -> List[Dict[str, Any]]:
    tbl = db.table("global_local_privilege")
    if kind:
        return tbl.select(where={"kind": kind}, order_by="chemin_ref")
    return tbl.select(order_by="chemin_ref")


def list_privilege_conditions(db: Db, id_auth: int) -> List[Dict[str, Any]]:
    return db.table("global_local_privilege_condition").select(
        where={"id_auth": id_auth}, order_by="condition_id")


def list_supervisors(db: Db) -> List[Dict[str, Any]]:
    return db.table("global_local_security_supervisor").select(
        cols=["id", "supervisor_agent_id", "scope", "team_id", "active"],
        order_by="id")


def buffer_status(db: Db, external_tag: str = "", status: str = "") -> List[Dict[str, Any]]:
    w: Dict[str, Any] = {}
    if external_tag:
        w["external_tag"] = external_tag
    if status:
        w["status"] = status
    # ordre DESC impossible via Table.order_by LIMIT dans select (order_by str OK)
    where = w or None
    return db.table("global_local_buffer_op").select(
        where=where, order_by="op_id DESC", limit=500)
