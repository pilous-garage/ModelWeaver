"""hardware_software — domaine hardware_software.db : logiciels installés.

État RÉEL de la machine (distinct du catalogue local 'tool'/'tool_recipe'
qui sont les déclarations). Privilèges : tag par défaut hérité du catalogue,
réécrit par l'utilisateur via privilege_override.

Domaine OUVERT (installer, cli, agents). Pas de token.
"""

from __future__ import annotations

import os

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "hardware_software_schema.sql"


def db() -> Db:
    d = Db(db_path("hardware_software"), mode="w")
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    return Db(db_path("hardware_software"), mode="ro")


def _load_ddl() -> list:
    import pathlib
    return [pathlib.Path(os.path.join(os.path.dirname(__file__),
                                      _SCHEMA_FILE)).read_text()]


__all__ = ["db", "db_ro"]
