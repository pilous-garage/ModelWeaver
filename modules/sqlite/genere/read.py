"""read — lectures thin sur genere.db (gen_data + infra). Logique → genere.py.

V3 : id_data = INTEGER (uint hash stable(path_data)) ; path_data = texte
lisible "kind:scope:name". Les data_ref (FK) sont des id_data (int)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db
from modules.sqlite.genere.genere import data_id_of


def get_meta(db: Db, key: str) -> Optional[Any]:
    r = db.table("meta").get({"key": key}, cols=["value"])
    return r["value"] if r else None


def get_data(db: Db, project_id: int, path_data: str) -> Optional[Dict[str, Any]]:
    """get par path_data (texte). Résout id_data = hash(path_data)."""
    id_data = data_id_of(path_data)
    return db.table("gen_data").get({"project_id": project_id, "id_data": id_data})


def get_data_by_id(db: Db, project_id: int, id_data: int) -> Optional[Dict[str, Any]]:
    return db.table("gen_data").get({"project_id": project_id, "id_data": id_data})


def list_data(db: Db, project_id: int, kind: str = "",
              status: str = "") -> List[Dict[str, Any]]:
    where, args = ["project_id = ?"], [project_id]
    if kind:
        where.append("kind = ?"); args.append(kind)
    if status:
        where.append("status = ?"); args.append(status)
    w = "WHERE " + " AND ".join(where)
    return db.table("gen_data").sql(
        f"SELECT * FROM gen_data {w} ORDER BY updated_at DESC", args)


def list_backups(db: Db, project_id: int, backup_of_id: int) -> List[Dict[str, Any]]:
    return db.table("gen_data").select(
        where={"project_id": project_id, "backup_of_id": backup_of_id})


def get_config(db: Db, project_id: int, key: str) -> Optional[Dict[str, Any]]:
    return db.table("gen_config").get({"project_id": project_id, "key": key})


def list_config(db: Db, project_id: int = 0) -> List[Dict[str, Any]]:
    return db.table("gen_config").select(where={"project_id": project_id}, order_by="key")


def get_run(db: Db, project_id: int, data_ref: int, run_seq: int) -> Optional[Dict[str, Any]]:
    return db.table("gen_runs").get(
        {"project_id": project_id, "data_ref": data_ref, "run_seq": run_seq})


def list_runs(db: Db, project_id: int, data_ref: int) -> List[Dict[str, Any]]:
    return db.table("gen_runs").select(
        where={"project_id": project_id, "data_ref": data_ref}, order_by="run_seq")


def list_questions(db: Db, project_id: int = None) -> List[Dict[str, Any]]:
    t = db.table("questions")
    if project_id is None:
        return t.select(order_by="id_question")
    return t.sql("SELECT q.* FROM questions q JOIN gen_data g ON g.questionned=q.id_question "
                 "WHERE g.project_id = ? ORDER BY q.id_question", (project_id,))


def get_dependencies(db: Db, project_id: int, data_ref: int) -> List[Dict[str, Any]]:
    return db.table("gen_dependance").select(
        where={"project_id": project_id, "data_ref": data_ref}, order_by="role, dep_ref")


def get_dependents(db: Db, project_id: int, dep_ref: str) -> List[Dict[str, Any]]:
    return db.table("gen_dependance").select(
        where={"project_id": project_id, "dep_ref": dep_ref}, order_by="data_ref")


def runtime_globs(db: Db, project_id: int, data_ref: int) -> List[Dict[str, Any]]:
    return db.table("gen_runtime_files").select(
        where={"project_id": project_id, "data_ref": data_ref})


def get_file_stat(db: Db, project_id: int, ref_file: str) -> Optional[Dict[str, Any]]:
    return db.table("gen_fichier").get(
        {"project_id": project_id, "ref_file": ref_file},
        cols=["project_id", "ref_file", "last_modif_ts", "last_hash_ts", "last_hash"])


def list_file_stats(db: Db, project_id: int) -> List[Dict[str, Any]]:
    return db.table("gen_fichier").select(where={"project_id": project_id})


def get_file_rules(db: Db, project_id: int = 0) -> Optional[Dict[str, Any]]:
    return db.table("gen_file_rules").get({"project_id": project_id}, cols=["rules_json", "updated_at"])
