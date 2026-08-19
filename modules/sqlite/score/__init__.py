from __future__ import annotations

from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "score_schema.sql"
WRITE_SCORE_TOKEN = "write_score"


def db() -> Db:
    """Domaine `score` (score.db).
    
    Scores, buckets, benchmarks, efficacité.
    Writer dédié (scoreur).
    """
    d = Db(db_path("score"), mode="w", write_token=WRITE_SCORE_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    return Db(db_path("score"), mode="ro")


def get_writer(token: str = "") -> Db:
    d = Db(db_path("score"), mode="w", write_token=WRITE_SCORE_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl())
    if token != WRITE_SCORE_TOKEN:
        raise PermissionError("token writer score invalide")
    return d


def _load_ddl() -> list[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, _SCHEMA_FILE)) as f:
        return [f.read()]


__all__ = ["db", "db_ro", "get_writer", "WRITE_SCORE_TOKEN", "_SCHEMA_VERSION"]
