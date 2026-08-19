"""write — CRUD fin (écritures) sur les tables infra du domaine local.

Chaque appel exige le token du dedicated_writer (write_catalogue) sauf pour
les tables vraiment publiques ; la logique multi-étapes vit dans local.py.

import_local (buffer → local) : appartient au domaine local —
c'est le writer LOCAL qui applique les ops du buffer dans x_data (mini-batchs,
jamais bloquant). Il est exceptionnellement autorisé à marquer/supprimer des
lignes de buffer.db, MAIS uniquement via les fonctions dédiées du domaine
buffer (mark_status / purge_applied) sur une instance writer du buffer.

export_local (local → buffer) : les modifs locales à exporter sont déposées
dans le buffer (direction='out') via buffer/export_local ; le consumer OUT
(buffer → ailleurs) = buffer/export + mark_status après envoi."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from modules.sqlite.base import Db, WriteDenied
from modules.sqlite.buffer import write as buffer_write
from modules.sqlite.local import data_table
from modules.sqlite.local import local as L


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


def add_supervisor(db: Db, supervisor_agent_id: int,
                   scope: str = "global", team_id: int = None,
                   token: str = "") -> int:
    return db.table("global_local_security_supervisor").add(
        {"supervisor_agent_id": supervisor_agent_id, "scope": scope,
         "team_id": team_id}, token=token)


# ── Sources (déclaration) ─────────────────────────────────────

def add_source(db: Db, type_source: str, ref: str, label: str = "",
               token: str = "") -> Dict[str, Any]:
    """Déclare une source (user, official, distant…). ref = identifiant
    lisible de la source (ex. 'models.dev', 'github:org/repo')."""
    if type_source not in L.SOURCE_TYPES:
        raise ValueError(f"type_source inconnu: {type_source!r} "
                         f"(types: {', '.join(L.SOURCE_TYPES)})")
    db.table("global_local_source").upsert(
        {"type_source": type_source, "ref": ref, "label": label}, ["ref"],
        token=token)
    return {"ok": True, "type_source": type_source, "ref": ref}


def add_mirror(db: Db, source: Any, address: str,
               token: str = "") -> Dict[str, Any]:
    """Ajoute une adresse (mirroir) à une source."""
    sid = L.source_id_for(db, source)
    db.table("global_local_mirror_sources").upsert(
        {"sources_id": sid, "address": address}, ["sources_id", "address"],
        token=token)
    return {"ok": True, "sources_id": sid, "address": address}


# ── import_local (buffer → local) ─────────────────────────────

def import_local(buffer_w: Db, local_w: Db, limit: int = 500,
                 purge: bool = True, token: str = "") -> Dict[str, Any]:
    """import_local : consumer DU WRITER LOCAL — applique les ops pending
    du buffer dans x_data en mini-batchs (jamais bloquant — chaque op en
    try/except).

    Utilisation : le writer local possède les DEUX instances writer
    (buffer_w = buffer.get_writer(write_buffer) — token lié à l'instance ;
    local_w = writer local, token passé explicitement ou lié).

    - lecture des pending : buffer.db (ro, par l'instance writer buffer_w) ;
    - application dans local : DataTable (token write_catalogue) — la source
      de l'op vient du payload (défaut 'user') et le quadruplé porte la
      source ; une op ne touche que les lignes de SA source ;
    - marquage applied/error : buffer_write.mark_status — via le token LIÉ de
      buffer_w (autorisation spéciale : le local ne fait jamais d'écriture
      brute sur buffer.db) ;
    - purge=True → buffer_write.purge_applied (« j'ai fini, delete du
      buffer »)."""
    local_w.check_write(token or local_w._write_token)
    buffer_write.require_writer(buffer_w)
    tbl = buffer_w.table("buffer_op")
    pending = tbl.select(where={"status": "pending"}, order_by="op_id",
                         limit=limit)
    applied = errors = 0
    for row in pending:
        try:
            payload = json.loads(row["payload_json"] or "{}")
            _apply_op(local_w, row["domain"], row["op"], payload)
            buffer_write.mark_status(buffer_w, [row["op_id"]], "applied")
            applied += 1
        except Exception as e:
            buffer_write.mark_status(buffer_w, [row["op_id"]], "error",
                                     str(e)[:500])
            errors += 1
    left = tbl.count({"status": "pending"})
    purged = buffer_write.purge_applied(buffer_w) if purge else 0
    return {"ok": True, "applied": applied, "errors": errors,
            "pending_left": left, "purged": purged}


def _apply_op(db: Db, domain: str, op: str, payload: Dict[str, Any]) -> None:
    """Applique un op du buffer via la DataTable du domaine local. La source
    de l'op est portée par le payload (défaut 'user')."""
    if op == "delete":
        did = payload.get("data_id") or _data_id_of(payload)
        # Garde : ne supprime que la ligne de la source de l'op.
        row = db.table(f"{domain}_data").get({"data_id": did})
        if row:
            sid = L.source_id_for(db, payload.get("source", "user"))
            if row["source_id"] != sid:
                raise WriteDenied(
                    f"op delete refusé : {domain}/{did} est de source "
                    f"{L.source_type_of(db, row['source_id'])} (op source "
                    f"{L.source_type_of(db, sid)})")
            db.table(f"{domain}_data").remove({"data_id": did},
                                              token=db._write_token)
        return
    dt = data_table.get_table(db, domain)
    dt.upsert(payload, source=payload.get("source", "user"),
              token=db._write_token)
    tags = payload.get("tags") or {}
    if tags:
        # tags transportés par l'op — attachés sur la ligne exactement
        # écrite (résolution par quadruple, pas par accessor : les names
        # peuvent contenir des '/'). Garde : tag_type déclaré dans le
        # registre x_tag_type.
        sid = L.source_id_for(db, payload.get("source", "user"))
        did = L.data_id_of(payload.get("namespace", ""),
                           payload.get("name", ""), sid,
                           payload.get("version", "latest"))
        tag_tbl = db.table(f"{domain}_tag")
        regs = {r["tag_type"] for r in
                db.table(f"{domain}_tag_type").select(cols=["tag_type"])}
        for tag_type, tag_value in tags.items():
            if tag_type not in regs:
                raise ValueError(f"tag_type non déclaré: {tag_type!r}")
            tv = tag_value
            if isinstance(tv, (list, dict)):
                tv = json.dumps(tv, ensure_ascii=False)
            tag_tbl.upsert({"data_id": did, "tag_type": tag_type,
                            "tag_value": str(tv)},
                           ["data_id", "tag_type", "tag_value"],
                           token=db._write_token)


def _data_id_of(payload: Dict[str, Any]) -> int:
    return L.data_id_of(payload.get("namespace", ""),
                        payload.get("name", ""),
                        payload.get("source_id", 0),
                        payload.get("version", "latest"))
