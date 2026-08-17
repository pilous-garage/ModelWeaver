"""security — domaine security.db (autorisations filesystem par agent).

Unique table migrée depuis `agents.db` : `agent_fs_auth` (anciennement
gérée par `services.fs_auth` en SQL direct).

Domaine OUVERT (multi-écrivains : fs_auth, handlers auth…). Pas de token.
"""

from __future__ import annotations

from typing import List, Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

SCHEMA_VERSION = 1

SCHEMA: List[str] = [
    "CREATE TABLE IF NOT EXISTS agent_fs_auth ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  agent_id INTEGER NOT NULL,"
    "  root_path TEXT NOT NULL,"
    "  mode TEXT NOT NULL DEFAULT 'r',"
    "  UNIQUE(agent_id, root_path))",
]


class SecurityDomain:
    """Câble Db(security.db) + apply le schéma. Rien d'autre ici."""

    def __init__(self, write_token: str = ""):
        self.db = Db(db_path("security"), mode="w", write_token=write_token)
        self.db.create(SCHEMA_VERSION, SCHEMA)

    @property
    def fs_auth(self):
        return self.db.table("agent_fs_auth")


_domain: Optional["SecurityDomain"] = None


def get_domain() -> SecurityDomain:
    """Singleton paresseux (lazy)."""
    global _domain
    if _domain is None:
        _domain = SecurityDomain()
    return _domain
