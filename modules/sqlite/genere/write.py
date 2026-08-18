"""write — CRUD fin sur genere.db. La logique entity/refactoring → genere.py."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from modules.sqlite.base import Db


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _ts() -> float:
    return time.time()


def set_meta(db: Db, key: str, value: int, token: str = "") -> None:
    db.table("meta").upsert({"key": key, "value": value}, ["key"], token=token)


def upsert_data(db: Db, project_id: int, id_data: str, *,
                name: str = "", kind: str = "symbol", value: str = "",
                value_is_file: bool = False, dependencies_json: str = "[]",
                inputs_hash: str = "", status: str = "valid",
                token: str = "") -> Dict[str, Any]:
    now = _now()
    row = {"project_id": project_id, "id_data": id_data, "name": name or id_data,
           "kind": kind, "path": "", "ref_id": "",
           "value": value, "value_is_file": int(value_is_file),
           "dependencies_json": dependencies_json, "inputs_hash": inputs_hash,
           "status": status, "storage": "disk", "last_access_at": now,
           "nb_access": 0, "generated_at": now, "updated_at": now}
    db.table("gen_data").upsert(row, ["project_id", "id_data"], token=token)
    return {"ok": True, "project_id": project_id, "id_data": id_data}


def set_status(db: Db, project_id: int, id_data: str, status: str,
               token: str = "") -> int:
    return db.table("gen_data").update(
        {"project_id": project_id, "id_data": id_data},
        {"status": status, "updated_at": _now()}, token=token)


def set_inputs_hash(db: Db, project_id: int, id_data: str,
                    inputs_hash: str, token: str = "") -> int:
    return db.table("gen_data").update(
        {"project_id": project_id, "id_data": id_data},
        {"inputs_hash": inputs_hash, "status": "valid", "updated_at": _now()},
        token=token)


def bump_access(db: Db, project_id: int, id_data: str, token: str = "") -> int:
    return db.table("gen_data").sql(
        "UPDATE gen_data SET last_access_at = datetime('now'), "
        "nb_access = nb_access + 1 WHERE project_id = ? AND id_data = ?",
        (project_id, id_data), token=token)


def add_question(db: Db, question: str, token: str = "") -> int:
    return db.table("questions").add({"question": question}, token=token)


def flag_data_question(db: Db, project_id: int, id_data: str, id_question: int,
                       token: str = "") -> int:
    return db.table("gen_data").update(
        {"project_id": project_id, "id_data": id_data},
        {"questionned": id_question}, token=token)


def upsert_file_stat(db: Db, project_id: int, ref_file: str, *,
                     last_modif_ts: float = 0, last_hash: str = "",
                     token: str = "") -> None:
    db.table("gen_fichier").upsert(
        {"project_id": project_id, "ref_file": ref_file,
         "last_modif_ts": last_modif_ts,
         "last_hash_ts": _ts() if last_hash else 0,
         "last_hash": last_hash},
        ["project_id", "ref_file"], token=token)


def add_dependency(db: Db, project_id: int, data_ref: str, dep_ref: str,
                   dep_version: str = "", role: str = "implementation",
                   token: str = "") -> None:
    db.table("gen_dependance").upsert(
        {"project_id": project_id, "data_ref": data_ref, "dep_ref": dep_ref,
         "dep_version": dep_version, "role": role},
        ["project_id", "data_ref", "dep_ref", "dep_version"], token=token)


def register_runtime_glob(db: Db, project_id: int, data_ref: str, glob: str,
                          token: str = "") -> None:
    db.table("gen_runtime_files").upsert(
        {"project_id": project_id, "data_ref": data_ref, "glob": glob,
         "last_read_mtime": 0}, ["project_id", "data_ref", "glob"], token=token)


def set_config(db: Db, project_id: int, key: str, value: str,
               token: str = "") -> None:
    db.table("gen_config").upsert(
        {"project_id": project_id, "key": key, "value": value},
        ["project_id", "key"], token=token)


def delete_data(db: Db, project_id: int, id_data: str = "",
                backup_of: str = "", token: str = "") -> int:
    tbl = db.table("gen_data")
    if id_data:
        return tbl.remove({"project_id": project_id, "id_data": id_data}, token=token)
    if backup_of:
        return tbl.sql(
            "DELETE FROM gen_data WHERE project_id = ? AND backup_of = ?",
            (project_id, backup_of), token=token)
    return 0


def purge_project(db: Db, project_id: int, token: str = "") -> int:
    n = 0
    with db.in_write():
        for tbl in ("gen_data", "gen_runs", "gen_dependance", "gen_fichier",
                    "gen_runtime_files"):
            n += db.table(tbl).remove({"project_id": project_id}, token=token)
    return n
