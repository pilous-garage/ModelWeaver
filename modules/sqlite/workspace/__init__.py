"""workspace.db — projets, tâches, échanges, TaskFlow V0.15 (sub_tasks + relais).

Domaine OUVERT (WAL) : le supervisor + les agents écrivent ensemble.
La logique FSM/supervisor (TaskSupervisor.assign, release, supervise…) vit dans
services/task_supervisor — ce module ne fait que le CRUD fin sur les tables.
Les repos complets (WorkspaceDB/for_workspace) sont dans workspace/workspace.py.
"""
from __future__ import annotations

import os
from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 3
_SCHEMA_FILE = "workspace_schema.sql"


def _add_column_if_missing(db: Db, table: str, col: str, ddl: str) -> None:
    cols = {c["name"] for c in db.columns(table)}
    if col not in cols:
        db._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        db._conn.commit()


def _load_ddl() -> list[str]:
    with open(os.path.join(os.path.dirname(__file__), _SCHEMA_FILE)) as f:
        return [f.read()]


def _migrate_task_attachments(db: Db) -> None:
    """Fusion V2 : task_reports + task_files → task_attachments.

    Bases V1 qui ont encore les tables séparées : copie dans la table unifiée
    (text = content vide → path, file = path) puis suppression des anciennes.
    Idempotente : les anciennes tables sont DROPées après copie."""
    tables = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    if "task_attachments" not in tables:
        return
    if "task_reports" in tables:
        db._conn.execute("""
            INSERT INTO task_attachments (task_id, kind, role, content, path)
            SELECT task_id, 'text', role, content, '' FROM task_reports""")
        db._conn.execute("DROP TABLE task_reports")
    if "task_files" in tables:
        db._conn.execute("""
            INSERT OR IGNORE INTO task_attachments (task_id, kind, role, content, path)
            SELECT task_id, 'file', role, '', path FROM task_files""")
        db._conn.execute("DROP TABLE task_files")
    db._conn.commit()


def get_domain() -> Db:
    d = Db(db_path("workspace"), mode="w")
    d.create(_SCHEMA_VERSION, _load_ddl())
    # Migrations sur bases existantes (CREATE IF NOT EXISTS ne touche pas
    # les tables présentes) : colonnes ajoutées après la V1.
    _add_column_if_missing(d, "task_dependencies", "required_tag",
                           "TEXT DEFAULT ''")
    _migrate_task_attachments(d)
    return d


_domain: Optional[Db] = None


def db() -> Db:
    global _domain
    if _domain is None or getattr(_domain, "_closed", False):
        _domain = get_domain()
    return _domain


__all__ = ["db", "get_domain"]