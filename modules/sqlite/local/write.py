"""write — CRUD fin (écritures) sur les tables infra du domaine local.

Chaque appel exige le token du dedicated_writer (write_catalogue) sauf pour
les tables vraiment publiques ; la logique multi-étapes vit dans local.py."""

from __future__ import annotations

from typing import Any, Dict, List

from modules.sqlite.base import Db


def set_meta(db: Db, key: str, value: Any, token: str = "") -> None:
    db.table("global_local_meta").upsert(
        {"key": key, "value": value}, ["key"], token=token)


def activate_data_type(db: Db, code: str, active: bool, token: str = "") -> Dict[str, Any]:
    db.table("global_local_data_type").upsert(
        {"code": code, "active": 1 if active else 0}, ["code"], token=token)
    return {"ok": True, "type": code, "active": active}


def create_namespace(db: Db, ns: str, parent: str = "",
                     description: str = "", token: str = "") -> Dict[str, Any]:
    from modules.sqlite.local.local import is_reserved
    if not ns:
        raise ValueError("ns requis")
    if is_reserved(ns):
        raise ValueError(f"ns réservé: {ns!r}")
    if "/" in ns and not parent:
        parent = ns.rsplit("/", 1)[0]
    db.table("global_local_namespace").upsert(
        {"ns": ns, "parent": parent or None, "description": description},
        ["ns"], token=token)
    return {"status": "ok", "ns": ns, "parent": parent}


def delete_namespace(db: Db, ns: str, token: str = "") -> Dict[str, Any]:
    db.table("global_local_namespace").sql(
        "DELETE FROM global_local_namespace WHERE ns = ? OR ns LIKE ?",
        (ns, ns + "/%"), token=token)
    return {"ok": True, "ns": ns}


def create_path(db: Db, path_name: str, address: str,
                scheme: str = "file", description: str = "",
                token: str = "") -> Dict[str, Any]:
    if path_name.startswith("/$") or path_name.startswith("$"):
        raise ValueError("path ne commence jamais par /$ ou $")
    if "*" in path_name:
        raise ValueError("les écritures de chemin interdisent *")
    db.table("global_local_path").upsert(
        {"path_name": path_name, "address": address, "scheme": scheme,
         "description": description}, ["path_name"], token=token)
    return {"ok": True, "path_name": path_name}


def delete_path(db: Db, path_name: str, token: str = "") -> int:
    return db.table("global_local_path").remove({"path_name": path_name}, token=token)


def create_privilege(db: Db, chemin_ref: str, kind: str = "path",
                     agent_id: int = -1, team: int = -1, level: int = 1,
                     read: str = "----", write: str = "----",
                     exec: str = "----", privileged: str = "----",
                     ask: str = "none", deadline: str = "",
                     description: str = "", token: str = "") -> Dict[str, Any]:
    from modules.sqlite.local.local import ASK_LEVELS
    if ask not in ASK_LEVELS:
        raise ValueError(f"ask invalide: {ask!r}")
    mode = lambda s: (s or "----").ljust(4, "-")[:4]
    rid = db.table("global_local_privilege").add({
        "chemin_ref": chemin_ref, "kind": kind, "agent_id": agent_id,
        "team": team, "level": level, "read": mode(read),
        "write": mode(write), "exec": mode(exec), "privileged": mode(privileged),
        "ask": ask, "deadline": deadline or None,
        "description": description}, token=token)
    return {"status": "ok", "id_auth": rid, "chemin_ref": chemin_ref}


def set_shared_default(db: Db, type_: str, tag_type: str = "*",
                       tag_value: str = "*", can_be_shared: str = "non",
                       token: str = "") -> Dict[str, Any]:
    from modules.sqlite.local.local import SHARING_LEVELS
    if can_be_shared not in SHARING_LEVELS:
        raise ValueError(f"can_be_shared invalide: {can_be_shared!r}")
    dtid = db.table("global_local_data_type").get({"code": type_})
    if not dtid:
        raise KeyError(f"type inconnu: {type_}")
    db.table("global_local_shared_default").upsert(
        {"data_type_id": dtid["data_type_id"], "tag_type": tag_type,
         "tag_value": tag_value, "can_be_shared": can_be_shared},
        ["data_type_id", "tag_type", "tag_value"], token=token)
    return {"ok": True, "type": type_}


def buffer_push(db: Db, ops: List[Dict[str, Any]],
                external_tag: str = "", token: str = "") -> Dict[str, Any]:
    """Dépose N ops 'pending' (importeur externe) — UNE écriture batch."""
    db.check_write(token)
    if not ops:
        return {"ok": True, "pushed": 0}
    rows = [{"direction": "in", "domain": o.get("domain", ""),
             "op": o.get("op", "add"),
             "payload_json": __import__("json").dumps(o.get("payload", {})),
             "external_tag": external_tag,
             "ref_external": o.get("ref_external", "")} for o in ops]
    db.table("global_local_buffer_op").add(rows, token=token)
    return {"ok": True, "pushed": len(ops)}


def add_supervisor(db: Db, supervisor_agent_id: int,
                   scope: str = "global", team_id: int = None,
                   token: str = "") -> int:
    return db.table("global_local_security_supervisor").add(
        {"supervisor_agent_id": supervisor_agent_id, "scope": scope,
         "team_id": team_id}, token=token)
