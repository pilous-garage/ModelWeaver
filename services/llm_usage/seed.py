"""llm_usage/seed — seed des budgets et coûts (Idée 18).

Relie les adresses runtime aux budgets/coûts pour que la CONSOMMATION
(consume_call) soit effective :

  1. Pour chaque api_key_tag distinct → budget_generique_key_tag
     (req_per_day, tok_per_day, cost_per_day) avec quotas par défaut.
  2. Pour chaque adresse (provider_model_address) → cost_key_tag :
     coûts PAR TAG (tok_in→money, tok_out→money, request→money) dont les
     ratios viennent des prix provider_models (cost_per_input/output_token).
  3. budget_final par adresse_runtime : intersection générique ∩ user
     (pas de budget_user ici → seul le générique).
  4. cost_final par adresse_runtime : dérivé du cost_key_tag.

Idempotent : ne réinsère pas ce qui existe (INSERT OR IGNORE). Best-effort.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Optional

# Quotas par défaut par tag (conservateurs, avant les vraies clés).
# souplesse doit être dans ('strict','souple','informatif') — cf. le CHECK
# de budget_generique_key_tag.
DEFAULT_BUDGETS = [
    # (tag_code, interval, quota_effectif, souplesse, souplesse_taux)
    ("req_per_day",  "day", 100000,  "souple", 0.5),     # 100k req/jour
    ("tok_per_day",  "day", 1000000000, "souple", 0.5),  # 1Md tokens/jour
    ("cost_per_day", "day", 100.0,   "souple", 0.5),     # 100 $/jour
]


def _tag_id(cat, code: str) -> int:
    row = cat.conn.execute(
        "SELECT id FROM budget_tags WHERE code = ?", (code,)).fetchone()
    return row["id"] if row else 0


def _price_ratios(cat, adresse_id: int):
    """(ratio_tok_in_money, ratio_tok_out_money) pour une adresse, depuis
    provider_models. Retourne des 0.0 si tarifs manquants."""
    row = cat.conn.execute("""
        SELECT pm.cost_per_input_token, pm.cost_per_output_token
        FROM provider_model_address a
        JOIN provider_models pm ON pm.id = a.provider_model_id
        WHERE a.adresse_id = ?
        LIMIT 1
    """, (adresse_id,)).fetchone()
    if not row:
        return (0.0, 0.0)
    cin, cout = row["cost_per_input_token"], row["cost_per_output_token"]
    try:
        return (float(cin or 0.0), float(cout or 0.0))
    except (TypeError, ValueError):
        return (0.0, 0.0)


def _budget_generique_for_tag(cat, api_key_tag: str) -> dict:
    """Assure le budget_generique_key_tag pour un tag. Retourne les
    budget_generique_key_tag_id par tag_code."""
    out = {}
    for tag_code, interval, quota, souplesse, taux in DEFAULT_BUDGETS:
        tid = _tag_id(cat, tag_code)
        if not tid:
            continue
        cur = cat.conn.execute("""
            INSERT OR IGNORE INTO budget_generique_key_tag
                (api_key_tag, tag_id, interval_reset, quota, souplesse,
                 souplesse_taux, next_reset)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (api_key_tag, tid, interval, quota, souplesse, taux,
              int(time.time())))
        row = cat.conn.execute("""
            SELECT budget_generique_key_tag_id FROM budget_generique_key_tag
            WHERE api_key_tag = ? AND tag_id = ? AND interval_reset = ?
        """, (api_key_tag, tid, interval)).fetchone()
        if row:
            out[tag_code] = row["budget_generique_key_tag_id"]
    cat.conn.commit()
    return out


def _cost_key_tag_for_adresse(cat, adresse_id: int, tag: str,
                              generique_by_tag: dict) -> None:
    """Assure les cost_key_tag d'une adresse (tok_in→money, tok_out→money,
    request→money) liés au budget générique de son tag."""
    rin, rout = _price_ratios(cat, adresse_id)
    per_tag = generique_by_tag.get(tag) or {}
    for unit_in, unit_out, ratio in (
            ("tok_in", "money", rin),
            ("tok_out", "money", rout),
            ("request", "money", 0.0),
            ("secondes", "time", 1.0),
            ("thinking_in", "money", 0.0),
            ("tok_in", "time", 0.0),
    ):
        if ratio is None or ratio <= 0:
            continue
        gid = per_tag.get("cost_per_day")
        if not gid:
            continue
        cat.conn.execute("""
            INSERT OR IGNORE INTO cost_key_tag
                (adresse_key_tag_id, budget_generique_key_tag_id,
                 unit_in, unit_out, ratio)
            VALUES (?, ?, ?, ?, ?)
        """, (adresse_id, gid, unit_in, unit_out, ratio))
    cat.conn.commit()


def _budget_final_for_runtime(cat, adr_runtime_id: int, adresse_id: int,
                              api_key_tag: str,
                              per_tag: dict) -> dict:
    """Assure les budget_final d'une adresse_runtime (intersection
    générique ∩ user). Pas de budget_user ici → seul le générique.
    `per_tag` = le dict {tag_code: budget_generique_key_tag_id} du TAG."""
    bf_by_tag = {}
    for tag_code, interval, quota, souplesse, taux in DEFAULT_BUDGETS:
        gid = per_tag.get(tag_code)
        tid = _tag_id(cat, tag_code)
        if not gid or not tid:
            continue
        # quota effectif = min(générique, user). Pas de user → quota générique.
        cat.conn.execute("""
            INSERT OR IGNORE INTO budget_final
                (adresse_runtime_id, budget_generique_key_tag_id,
                 budget_user_key_id, tag_id, quota_effectif, spent, souplesse,
                 souplesse_taux, interval_reset, next_reset)
            VALUES (?, ?, NULL, ?, ?, 0, ?, ?, ?, ?)
        """, (adr_runtime_id, gid, tid, quota, souplesse, taux, interval,
              int(time.time())))
        row = cat.conn.execute("""
            SELECT budget_final_id FROM budget_final
            WHERE adresse_runtime_id = ? AND tag_id = ? AND interval_reset = ?
        """, (adr_runtime_id, tid, interval)).fetchone()
        if row:
            bf_by_tag[tag_code] = row["budget_final_id"]
    cat.conn.commit()
    return bf_by_tag


def _cost_final_for_runtime(cat, adr_runtime_id: int,
                            bf_by_tag: dict) -> None:
    """Assure les cost_final d'une adresse_runtime : dérivés du cost_key_tag
    (mêmes unit_in/out/ratio) pointant vers le budget_final correspondant."""
    rin, rout = _price_ratios(cat, _adresse_for_runtime(cat, adr_runtime_id))
    rows = [
        ("tok_in", "money", rin, "tok_per_day", "cost_per_day"),
        ("tok_out", "money", rout, "tok_per_day", "cost_per_day"),
        ("request", "money", 0.0, "req_per_day", "cost_per_day"),
        ("secondes", "time", 1.0, "req_per_day", "req_per_day"),
        ("thinking_in", "money", 0.0, "tok_per_day", "cost_per_day"),
    ]
    for unit_in, unit_out, ratio, bg_tag, bf_tag in rows:
        if ratio is None or ratio <= 0:
            continue
        bf_id = bf_by_tag.get(bf_tag)
        if not bf_id:
            continue
        cat.conn.execute("""
            INSERT OR IGNORE INTO cost_final
                (adresse_runtime_id, budget_final_id, unit_in, unit_out, ratio)
            VALUES (?, ?, ?, ?, ?)
        """, (adr_runtime_id, bf_id, unit_in, unit_out, ratio))
    cat.conn.commit()


def _adresse_for_runtime(cat, adr_runtime_id: int) -> int:
    row = cat.conn.execute(
        "SELECT adresse_id FROM adresse_runtime WHERE adresse_runtime_id = ?",
        (adr_runtime_id,)).fetchone()
    return row["adresse_id"] if row else 0


def seed_budgets(cat=None, tags=None):
    """Seed idempotent des budgets/coûts pour toutes les adresses runtime.

    `tags` : liste optionnelle de api_key_tag à traiter (None = tous).
    Best-effort, ne lève jamais.
    """
    try:
        if cat is None:
            from modules.sql.catalogue_repo import CatalogueDB
            cat = CatalogueDB()
        if tags is not None:
            placeholders = ",".join("?" * len(tags))
            adresses = cat.conn.execute(
                f"SELECT adresse_id, api_key_tag FROM provider_model_address "
                f"WHERE api_key_tag IN ({placeholders}) ORDER BY adresse_id",
                tuple(tags)).fetchall()
        else:
            adresses = cat.conn.execute("""
                SELECT a.adresse_id, a.api_key_tag
                FROM provider_model_address a
                ORDER BY a.adresse_id
            """).fetchall()
        if not adresses:
            return {"ok": True, "adresses": 0}

        done = 0
        generique_by_tag = {}
        for adr in adresses:
            adr_id, tag = adr["adresse_id"], adr["api_key_tag"]
            if tag not in generique_by_tag:
                generique_by_tag[tag] = _budget_generique_for_tag(cat, tag)
            # cost_key_tag pour l'adresse
            _cost_key_tag_for_adresse(cat, adr_id, tag, generique_by_tag)
            # budget_final + cost_final pour chaque adresse_runtime du tag
            for ar in cat.conn.execute(
                    "SELECT adresse_runtime_id FROM adresse_runtime "
                    "WHERE adresse_id = ?", (adr_id,)).fetchall():
                ar_id = ar["adresse_runtime_id"]
                per_tag = generique_by_tag.get(tag) or {}
                bf = _budget_final_for_runtime(
                    cat, ar_id, adr_id, tag, per_tag)
                _cost_final_for_runtime(cat, ar_id, bf)
            done += 1
        return {"ok": True, "adresses": done}
    except Exception:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return {"ok": False, "adresses": 0}


if __name__ == "__main__":
    r = seed_budgets()
    print("seed_budgets:", r)
