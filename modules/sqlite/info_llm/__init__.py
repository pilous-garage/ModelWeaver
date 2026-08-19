"""info_llm.db — données STABLES pour le bridge (projection du local v4).

Un SEUL writer : le COMPACTEUR (token write_info_llm, mini-batchs upsert).
Le bridge ne lit JAMAIS local_catalogue : il ne voit que ces tables, avec
les ids stables qu'il aime (ints par réf, conservés entre régénérations).

Toute table est soit :
  - une PROJECTION régénérée par le compacteur (colonnes local_data_id qui
    tracent la provenance v4) ;
  - une table d'ANNOTATIONS manuelles (alias, définitions de budgets),
    jamais écrasées par le compacteur.

Le compacteur vit dans modules/sqlite/info_llm/compact.py ; le writer dans
write.py (regenerate en mini-batchs) ; read.py = lectures du bridge.
"""
from __future__ import annotations

from typing import Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 3
_SCHEMA_FILE = "info_llm_schema.sql"
WRITE_INFO_LLM_TOKEN = "write_info_llm"


def db() -> Db:
    """Domaine `info_llm` (info_llm.db), ouvert en w avec le token du
    compacteur (seul écrit) — les lecteurs ouvrent leur propre ro."""
    d = Db(db_path("info_llm"), mode="w",
           write_token=WRITE_INFO_LLM_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    """Lecteur : info_llm.db en lecture seule (pas de token)."""
    return Db(db_path("info_llm"), mode="ro")


def get_writer(token: str = "") -> Db:
    """Writer configuré : exige le token du compacteur (write_info_llm)."""
    d = Db(db_path("info_llm"), mode="w",
           write_token=WRITE_INFO_LLM_TOKEN)
    d.create(_SCHEMA_VERSION, _load_ddl())
    if token != WRITE_INFO_LLM_TOKEN:
        raise PermissionError("token writer info_llm invalide")
    return d


def _load_ddl() -> list[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, _SCHEMA_FILE)) as f:
        return [f.read()]


__all__ = ["db", "db_ro", "get_writer", "WRITE_INFO_LLM_TOKEN"]
