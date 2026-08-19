from __future__ import annotations

from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "task_schema.sql"
WRITE_TASK_TOKEN = "write_task"


def db() -> Db:
    """Domaine `task` (task.db).

    Writer dédié write_task (tâches, sub_tasks, ask_new_task ; le consumer IN
    de buffer reste dans le domaine local). Lectures mode=ro par db_ro().
    """
    d = Db(db_path("task"), mode="w", write_token=WRITE_TASK_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    """Lecteur : task.db en lecture seule (pas de token)."""
    return Db(db_path("task"), mode="ro")


def get_writer(token: str = "") -> Db:
    """Writer configuré : exige le token du writer task."""
    d = Db(db_path("task"), mode="w", write_token=WRITE_TASK_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl())
    if token != WRITE_TASK_TOKEN:
        raise PermissionError("token writer task invalide")
    return d


def _load_ddl() -> list[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, _SCHEMA_FILE)) as f:
        return [f.read()]


__all__ = ["db", "db_ro", "get_writer", "WRITE_TASK_TOKEN"]