"""migrate_addresses — migre les tables de log/score/usage vers adresse_id.

Remplace les clés textes (provider_ref/model_ref) par `adresse_id` (répertoire
provider_model_address). Tables traitées : score_batch, score_batch_blocks_*,
usage_history_*, model_bucket_counts, real_call_models, llm_caller_sessions,
model_success_runs, endpoint_model_usage.

Les tables par MODÈLE (score_benchmark_etire, local_model_efficacy) restent
par model_id (le benchmark est un score du modèle, pas d'une adresse).

Stratégie : pour chaque ligne, résout (provider_ref, model_ref) → adresse_id
via le répertoire. Les lignes non résolues (variantes) tentent une résolution
par model_key ; les vraiment non résolues sont purgées (table orphelins).

Usage:
    python3 services/llm_allocation/migrate_addresses.py [--dry-run]
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from modules.sql.catalogue_repo import CatalogueDB
from services._common import runtime_db_path
from services.llm_allocation.address import ensure_addresses, resolve_address, \
    _model_key_for

# Tables avec (provider_ref, model_ref) à migrer.
# (table, [colonnes_adresse_id], est_indexe_par_agent)
TABLES = [
    ("score_batch", False),
    ("score_batch_blocks_5m", False),
    ("score_batch_blocks_1h", False),
    ("score_batch_blocks_1d", False),
    ("model_bucket_counts", False),
    ("real_call_models", False),
    ("llm_caller_sessions", False),
    ("model_success_runs", False),
    ("usage_history_1m", True),
    ("usage_history_15m", True),
    ("usage_history_1h", True),
    ("usage_history_3h", True),
    ("usage_history_1d", True),
    ("usage_history_1w", True),
    ("usage_history_1mo", True),
    ("endpoint_model_usage", True),
]


def _resolve_rows(cat, provider_ref, model_ref):
    """Résout (provider_ref, model_ref) → adresse_id, avec fallback model_key."""
    aid = resolve_address(provider_ref, model_ref, cat)
    if aid is not None:
        return aid
    # variante : tente par model_key
    _mid, mkey = _model_key_for(cat, model_ref)
    if mkey:
        row = cat.conn.execute(
            "SELECT adresse_id FROM provider_model_address "
            "WHERE provider_ref = ? AND model_key = ? LIMIT 1",
            (provider_ref, mkey)).fetchone()
        if row:
            return row["adresse_id"]
    return None


def migrate(cat=None, dry_run: bool = False) -> dict:
    cat = cat or CatalogueDB()
    ensure_addresses(cat)
    rt_path = runtime_db_path()
    import sqlite3
    rt = sqlite3.connect(rt_path)
    rt.row_factory = sqlite3.Row

    # cache de résolution (provider_ref/model_ref → adresse_id)
    cache: dict = {}
    migrated = skipped = orphaned = 0
    orphan_rows = []

    for table, _has_agent in TABLES:
        cols = [c[1] for c in rt.execute(f"PRAGMA table_info({table})").fetchall()]
        has_p = "provider_ref" in cols
        has_m = "model_ref" in cols
        if not (has_p and has_m):
            continue
        # ajoute la colonne adresse_id si absente
        if "adresse_id" not in cols:
            if dry_run:
                print(f"  [dry] {table}: colonne adresse_id à ajouter")
                continue
            rt.execute(f"ALTER TABLE {table} ADD COLUMN adresse_id INTEGER")
        else:
            # colonne présente : skip si DÉJÀ migrée (au moins une ligne adresse_id>0),
            # sinon re-migre (le run précédent a pu créer la colonne sans commit).
            done = rt.execute(
                f"SELECT COUNT(*) n FROM {table} WHERE adresse_id > 0").fetchone()[0]
            if done > 0:
                print(f"  {table}: déjà migrée (skip, {done})")
                continue
        # migre les lignes
        rows = rt.execute(
            f"SELECT rowid AS _rid, provider_ref, model_ref FROM {table}").fetchall()
        t_mig = t_orph = 0
        for r in rows:
            p = r["provider_ref"] or ""
            m = r["model_ref"] or ""
            key = (p, m)
            aid = cache.get(key)
            if aid is None and key not in cache:
                aid = _resolve_rows(cat, p, m)
                cache[key] = aid
            if aid is None:
                orphaned += 1
                t_orph += 1
                if len(orphan_rows) < 10:
                    orphan_rows.append(f"{p}/{m}")
                if not dry_run:
                    rt.execute(
                        f"UPDATE {table} SET adresse_id = 0 WHERE rowid = ?",
                        (r["_rid"],))
                continue
            if not dry_run:
                rt.execute(
                    f"UPDATE {table} SET adresse_id = ? WHERE rowid = ?",
                    (aid, r["_rid"]))
            migrated += 1
            t_mig += 1
        if not dry_run:
            rt.commit()  # commit par table (le crash d'une table ne perd pas les autres)
        print(f"  {table}: {t_mig} migrées, {t_orph} orphelines")
    rt.close()
    return {"migrated": migrated, "orphaned": orphaned,
            "cache_size": len(cache), "orphan_examples": orphan_rows[:8]}


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    cat = CatalogueDB()
    print("Table adresse :", ensure_addresses(cat))
    print("Migration", "(dry-run)" if dry else "", ":", migrate(cat, dry_run=dry))
    cat.close()
