"""budget_cost — domaine budget_cost.db : sources NON-runtime des budgets/couts.

Hiérarchie : humain > general > guess (guess = quotas seulement).
Limites par budget : budget_reel / budget_hard (% refus) / budget_soft (% toléré).
cost_init/quota_init sont des data_types du catalogue local (buffer models.dev) ;
ce domaine les lit pour générer runtime_llm.budget_final.

Domaine OUVERT (cli, agents, daemon). Pas de token.
"""

from __future__ import annotations

import os

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path

_SCHEMA_VERSION = 1
_SCHEMA_FILE = "budget_cost_schema.sql"


def db() -> Db:
    d = Db(db_path("budget_cost"), mode="w")
    d.create(_SCHEMA_VERSION, _load_ddl())
    return d


def db_ro() -> Db:
    return Db(db_path("budget_cost"), mode="ro")


def _load_ddl() -> list:
    import pathlib
    return [pathlib.Path(os.path.join(os.path.dirname(__file__),
                                      _SCHEMA_FILE)).read_text()]


__all__ = ["db", "db_ro"]
