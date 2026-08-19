from __future__ import annotations

from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "runtime_llm_schema.sql"


def db() -> Db:
    """Domaine `runtime_llm` (runtime_llm.db).
    
    Volatil, écrit par le bridge à chaque appel (per-line, WAL).
    Domaine OUVERT : pas de token, écritures libres par les threads bridge.
    """
    d = Db(db_path("runtime_llm"), mode="w")
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    """Lecteur : runtime_llm.db en lecture seule."""
    return Db(db_path("runtime_llm"), mode="ro")


def _load_ddl() -> list[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, _SCHEMA_FILE)) as f:
        return [f.read()]


__all__ = ["db", "db_ro", "_SCHEMA_VERSION"]
