from __future__ import annotations

from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "buffer_schema.sql"
WRITE_BUFFER_TOKEN = "write_buffer"


def db() -> Db:
    """Domaine `buffer` (buffer.db).

    Ouvert en mode `w` avec le token du writer buffer : dépôt des ops
    (importeurs externes) + marquage des status par le writer local (consumer
    IN, autorisation spéciale). Les lecteurs ouvrent leur propre Db(mode='ro').
    """
    d = Db(db_path("buffer"), mode="w", write_token=WRITE_BUFFER_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    """Lecteur : buffer.db en lecture seule (pas de token)."""
    return Db(db_path("buffer"), mode="ro")


def get_writer(token: str = "") -> Db:
    """Writer configuré : exige le token du writer buffer (dépôt d'ops)."""
    d = Db(db_path("buffer"), mode="w", write_token=WRITE_BUFFER_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl())
    if token != WRITE_BUFFER_TOKEN:
        raise PermissionError("token writer buffer invalide")
    return d


def _load_ddl() -> list[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, _SCHEMA_FILE)) as f:
        return [f.read()]


__all__ = ["db", "db_ro", "get_writer", "WRITE_BUFFER_TOKEN"]