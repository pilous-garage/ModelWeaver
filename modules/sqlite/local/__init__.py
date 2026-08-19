from __future__ import annotations

from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 4
_SCHEMA_FILE = "local_schema.sql"
WRITE_CATALOGUE_TOKEN = "write_catalogue"


def db() -> Db:
    """Domaine `local` (local_catalogue.db).

    Ouvert en mode `w` avec le token du dedicated_writer : c'est le SEUL writer
    du domaine (create_type, upsert, écriture de privileges, consumer IN du
    buffer). Les lecteurs ouvrent leur propre Db(mode='ro').
    """
    d = Db(db_path("local_catalogue"), mode="w", write_token=WRITE_CATALOGUE_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl(), meta_table="global_local_meta",
             reset_below=4)
    return d


def db_ro() -> Db:
    """Lecteur : local_catalogue.db en mode lecture seule (pas de token)."""
    return Db(db_path("local_catalogue"), mode="ro")


def _load_ddl() -> list[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, _SCHEMA_FILE)) as f:
        return [f.read()]


_DOMAIN: Optional[Db] = None
_DOMAIN_LOCK = False


def get_domain() -> Db:
    global _DOMAIN
    if _DOMAIN is None:
        _DOMAIN = db()
    return _DOMAIN


def get_writer(token: str = "") -> Db:
    """Writer configuré : exige le token du dedicated_writer (écritures)."""
    d = Db(db_path("local_catalogue"), mode="w", write_token=WRITE_CATALOGUE_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl(), meta_table="global_local_meta",
             reset_below=4)
    if token != WRITE_CATALOGUE_TOKEN:
        raise PermissionError("token writer local invalide")
    return d


__all__ = ["db", "db_ro", "get_domain", "get_writer", "WRITE_CATALOGUE_TOKEN"]
