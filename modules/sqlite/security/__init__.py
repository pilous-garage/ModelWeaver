"""security — domaine security.db (autorisations filesystem par agent).

Domaine OUVERT (multi-écrivains : fs_auth, handlers auth…). Pas de token
dédié : les écritures passent par db() ou get_domain(); les lectures par
db_ro().

Unique table migrée depuis `agents.db` : `agent_fs_auth` (anciennement
gérée par `services.fs_auth` en SQL direct).
"""
from __future__ import annotations

from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

from .security import SecurityDomain, get_domain, SCHEMA_VERSION


def db() -> Db:
    """Domaine `security` (security.db), ouvert en w (multi-écrivains, pas de token)."""
    d = Db(db_path("security"), mode="w")
    d.create(SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    """Lecteur : security.db en lecture seule (pas de token)."""
    return Db(db_path("security"), mode="ro")


def _load_ddl() -> list[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "security_schema.sql")) as f:
        return [f.read()]


__all__ = ["db", "db_ro", "SecurityDomain", "get_domain", "SCHEMA_VERSION"]