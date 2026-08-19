"""write — écritures du domaine info_llm (SEUL le compacteur écrit).

- `regenerate` : upsert mini-batchés (upsert_many) de plusieurs tables en
  UNE passe — garde token write_info_llm, une transaction par batch (WAL
  petit, reprise possible). Les ids existants sont CONSERVÉS (upsert par
  réf).
- Les tables d'annotations (alias_model, budget_tags, budgets,
  budget_generique_key_tag, budget_user_key_id) ne sont JAMAIS touchées :
  le compacteur ne les fournit pas.

Le bridge (threads agents) est LECTURE SEULE — il ouvre db_ro().
"""
from __future__ import annotations

from typing import Any, Dict, List

from modules.sqlite.base import Db
from modules.sqlite.info_llm import WRITE_INFO_LLM_TOKEN

DEFAULT_BATCH = 1000

# clés d'upsert par table (réfs stables) — les ids sont conservés
CONFLICT_COLS = {
    "catalogue_providers": ["ref"],
    "provider_endpoints": ["ref"],
    "catalogue_models": ["ref"],
    "model_capability": ["model_id", "capability"],
    "provider_models": ["provider_id", "name"],
    "provider_models_mapping": ["model_key", "provider_model_id"],
    "provider_endpoint_api_key_type": ["provider_id", "endpoint_id",
                                       "api_key_type"],
    "endpoint_apikeytype_model_adress": ["endpoint_id", "api_key_type",
                                         "provider_id", "model_endpoint_id"],
    "cost_final": ["model_id", "key_tag", "provider_model_id"],
}


def require_writer(db: Db, token: str = "") -> None:
    """Garde d'écriture : token explicite OU token lié de l'instance."""
    db.check_write(token or db._write_token)


def regenerate(db: Db, table_rows: Dict[str, List[Dict[str, Any]]],
               batch: int = DEFAULT_BATCH, token: str = "") -> Dict[str, Any]:
    """Régénère des tables info_llm par upsert mini-batchés (UNE passe).

    table_rows : {table: [rows…]} — chaque row porte ses colonnes dont les
    réfs UNIQUE ; les ids existants sont conservés. `CONFLICT_COLS` donne
    les clés d'upsert (toute autre table = erreur).
    Retourne {table: count} par table."""
    require_writer(db, token)
    token = token or db._write_token
    out: Dict[str, int] = {}
    for table, rows in table_rows.items():
        if not rows:
            continue
        conflict = CONFLICT_COLS.get(table)
        if conflict is None:
            raise ValueError(
                f"table non régénérable: {table!r} (pas dans CONFLICT_COLS)")
        tbl = db.table(table)
        for i in range(0, len(rows), batch):
            tbl.upsert_many(rows[i:i + batch], conflict, token=token)
        out[table] = len(rows)
    return {"ok": True, "tables": out}
