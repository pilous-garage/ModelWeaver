from __future__ import annotations

import os
from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 3
_SCHEMA_FILE = "genere_schema.sql"


def get_domain() -> Db:
    """Domaine `genere` (catalogue_genere.db) — OUVERT (multi-écrivains, WAL).

    Cache de données CALCULÉES (symbols/pétri/contracts/export…) dérivées de
    fichiers + du local_catalogue. Le cache ram/disque + thread flusher V2 est
    temporairement défalqué : disque WAL = source de vérité, recalcul idempotent
    en cas de course d'écriture.
    """
    d = Db(db_path("catalogue_genere"), mode="w")
    if not os.path.exists(os.path.join(os.path.dirname(__file__), _SCHEMA_FILE)):
        return d
    with open(os.path.join(os.path.dirname(__file__), _SCHEMA_FILE)) as f:
        d.create(_SCHEMA_VERSION, [f.read()])
    return d


_domain: Optional[Db] = None


def db() -> Db:
    global _domain
    if _domain is None:
        _domain = get_domain()
    return _domain


__all__ = ["db", "get_domain"]
