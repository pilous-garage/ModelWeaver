"""dialogue_agent.db — dialogues : chatroom arborescent, échanges 1:1
inter-agents (flushables), humain = agent NÉGATIF, escalade humain, consensus.
Une team switche de projet (le dialogue suit le project_id) ; plusieurs teams
peuvent discuter sur le même projet ; l'inter-swarm distant est prévu
(agent_id/négatifs = autres nœuds, ids globaux dans les messages directs).

Domaine OUVERT (pas de writer dédié, pas de token) : read.py / write.py
exposent les fonctions. Si spam/attaque → on ajoutera un writer qui filtre
les signaux (les sections 2-4 de write.py sont prévues pour ça).
"""
from __future__ import annotations

import os

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 2
_SCHEMA_FILE = "dialogue_agent_schema.sql"


def db() -> Db:
    d = Db(db_path("dialogue_agent"), mode="w")
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    """Lecteur : dialogue_agent.db en lecture seule."""
    return Db(db_path("dialogue_agent"), mode="ro")


def _load_ddl() -> list[str]:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, _SCHEMA_FILE)) as f:
        return [f.read()]


__all__ = ["db", "db_ro"]