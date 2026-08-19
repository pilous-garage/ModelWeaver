"""services — domaine services.db : annuaire des services + état des ticks.

Les tables service_ticks/_secondes/_runs reprennent EXACTEMENT les colonnes
que le ServiceTicker créait en dur dans modelweaver.db : la migration ne
change que le support de stockage (services.db), pas le contrat.

Domaine OUVERT (multi-écrivains : daemon, ticker, cli, agents). Pas de token.
"""

from __future__ import annotations

import os

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "services_schema.sql"


def db() -> Db:
    d = Db(db_path("services"), mode="w")
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    return Db(db_path("services"), mode="ro")


def _load_ddl() -> list:
    import pathlib
    return [pathlib.Path(os.path.join(os.path.dirname(__file__),
                                      _SCHEMA_FILE)).read_text()]


__all__ = ["db", "db_ro"]