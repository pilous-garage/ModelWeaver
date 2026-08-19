"""runtime — domaine runtime.db : état runtime du daemon + des services.

`service_runtime` est l'observatoire temps réel : pid_service, port_service,
last_tick/next_tick (dupliqués depuis le cache chaud du ticker), status.

Domaine OUVERT (multi-écrivains : daemon, ticker, watcher). Pas de token.
"""

from __future__ import annotations

import os

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "runtime_schema.sql"


def db() -> Db:
    d = Db(db_path("runtime"), mode="w")
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    return Db(db_path("runtime"), mode="ro")


def _load_ddl() -> list:
    import pathlib
    return [pathlib.Path(os.path.join(os.path.dirname(__file__),
                                      _SCHEMA_FILE)).read_text()]


__all__ = ["db", "db_ro"]