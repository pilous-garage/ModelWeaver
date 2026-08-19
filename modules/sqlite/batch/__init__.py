from __future__ import annotations

from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "batch_schema.sql"
WRITE_BATCH_TOKEN = "write_batch"


def db() -> Db:
    """Domaine `batch` (batch.db).
    
    Agrégations d'usage, séquences de succès, rapports d'archive.
    Writer dédié (batcheur) — lecture par le scoreur et le bridge.
    """
    d = Db(db_path("batch"), mode="w", write_token=WRITE_BATCH_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    return Db(db_path("batch"), mode="ro")


def get_writer(token: str = "") -> Db:
    d = Db(db_path("batch"), mode="w", write_token=WRITE_BATCH_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl())
    if token != WRITE_BATCH_TOKEN:
        raise PermissionError("token writer batch invalide")
    return d


def _load_ddl() -> list[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, _SCHEMA_FILE)) as f:
        return [f.read()]


__all__ = ["db", "db_ro", "get_writer", "WRITE_BATCH_TOKEN", "_SCHEMA_VERSION"]
