"""agent — domaine agents.db (identité + runtime des agents).

Domaine OUVERT (multi-écrivains : agent_manager, security, auth, dev_chat,
task_supervisor). Pas de token dédié : les écritures passent par db() ou
get_domain(); les lectures par db_ro().

Les 4 tables obsolètes (wait_for, team_tasks, conversations,
conversation_messages) sont DROPPÉES à l'application du schéma
(schema_version 1 -> 2).
"""
from __future__ import annotations

from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

from .agent import AgentDomain, get_domain, SCHEMA_VERSION


def db() -> Db:
    """Domaine `agent` (agents.db), ouvert en w (multi-écrivains, pas de token)."""
    d = Db(db_path("agents"), mode="w")
    d.create(SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    """Lecteur : agents.db en lecture seule (pas de token)."""
    return Db(db_path("agents"), mode="ro")


def _load_ddl() -> list[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "agent_schema.sql")) as f:
        return [f.read()]


__all__ = ["db", "db_ro", "AgentDomain", "get_domain", "SCHEMA_VERSION"]