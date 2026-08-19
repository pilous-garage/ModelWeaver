"""env — domaine env.db : environnement (env vars, paths canoniques, alias).

Domaine OUVERT (multi-écrivains : méthode __main__/boot, installer,
cli, agents). Pas de token : les écritures passent par db().

Le "path language" commun vit DANS ce domaine : `env.path.resolve()` est le
module PATH unique (dotted paths, alias) que toutes les couches utilisent.
"""

from __future__ import annotations

import os

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "env_schema.sql"


def db() -> Db:
    d = Db(db_path("env"), mode="w")
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    """Lecteur : env.db en lecture seule (pas de token)."""
    return Db(db_path("env"), mode="ro")


def _load_ddl() -> list:
    import pathlib
    return [pathlib.Path(os.path.join(os.path.dirname(__file__),
                                      _SCHEMA_FILE)).read_text()]


__all__ = ["db", "db_ro"]