"""agent — domaine agents.db (identité + runtime des agents).

Domaine OUVERT (multi-écrivains : agent_manager, security, auth, dev_chat,
task_supervisor). Pas de token.

Ce fichier ne contient QUE le SCHÉMA (CREATE) + le câblage Db. Toute la
logique SQL est dans modules.sqlite.base. Les 4 tables obsolètes
(wait_for, team_tasks, conversations, conversation_messages) sont DROPPÉES à
la migration (schema_version 1 -> 2).
"""

from __future__ import annotations

from typing import List, Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

SCHEMA_VERSION = 2


def _load_schema() -> List[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "agent_schema.sql")) as f:
        return [f.read()]


SCHEMA = _load_schema()


class AgentDomain:
    """Câble Db(agents.db) + apply le schéma. Rien d'autre ici."""

    def __init__(self, write_token: str = ""):
        self.db = Db(db_path("agents"), mode="w", write_token=write_token)
        self.db.create(SCHEMA_VERSION, SCHEMA)

    # raccourcis vers les tables (table_base)
    @property
    def agents(self):
        return self.db.table("agents")

    @property
    def runtime(self):
        return self.db.table("agent_runtime")

    @property
    def metrics(self):
        return self.db.table("agent_metrics")

    @property
    def signals(self):
        return self.db.table("agent_signals")

    @property
    def entrypoints(self):
        return self.db.table("agent_entrypoints")

    @property
    def auth_requests(self):
        return self.db.table("auth_requests")

    @property
    def meta(self):
        return self.db.table("meta")


_domain: Optional["AgentDomain"] = None


def get_domain() -> AgentDomain:
    """Singleton paresseux (lazy) — évite la création DB à l'import."""
    global _domain
    if _domain is None:
        _domain = AgentDomain()
    return _domain
