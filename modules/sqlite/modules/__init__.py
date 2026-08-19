"""modules — domaine modules.db : annuaire des modules + routes exposées.

Module PATH : l'accès par chemin dotted (modules.sqlite.services.add_service,
services.service_tick, alias mw.svc...) passe par `env.path` (résolveur) ou
`modules.call()` (résolveur + vérification de la route en base).

Domaine OUVERT (multi-écrivains : discover, boot, cli). Pas de token.
"""

from __future__ import annotations

import os

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "modules_schema.sql"


def db() -> Db:
    d = Db(db_path("modules"), mode="w")
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    return Db(db_path("modules"), mode="ro")


def _load_ddl() -> list:
    import pathlib
    return [pathlib.Path(os.path.join(os.path.dirname(__file__),
                                      _SCHEMA_FILE)).read_text()]


__all__ = ["db", "db_ro"]