#!/usr/bin/env python3
"""Construit la table alias_model : noms d'un modèle chez les sources externes.

Pour chaque source (provider ou benchmark), on enregistre chaque nom de modèle
observé et on le relie (ou non) à notre model_id du catalogue :

  - source_type='benchmark' : refs distinctes de model_benchmarks_raw
    (artificial_analysis, lmsys_arena, lmsys_arena_elo, arena_hard_auto,
     swe_bench_verified, open_llm_leaderboard…)
  - source_type='provider'  : provider_model_name de provider_models
    (le nom exposé par chaque provider : google, nvidia, openrouter…)

Résolution par model_key normalisé (+ alias explicites si présents).
status : linked (résolu vers un model_id) / unresolved (aucun match).

Usage :
    python3 scripts/build_aliases.py            # remplit catalogue.db
    python3 scripts/build_aliases.py --report   # affiche les stats + résumés
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scraper-base"))

from benchmarks.model_key import model_key  # noqa: E402
from modules.sql.db import CatalogueDB  # noqa: E402


# Source benchmark → colonne à lire dans model_benchmarks_raw.
BENCHMARK_SOURCES = [
    "artificial_analysis", "lmsys_arena", "lmsys_arena_elo", "arena_hard_auto",
    "swe_bench_verified", "open_llm_leaderboard",
]


def _resolve_to_model_id(cat, name: str):
    """Résout un nom source vers notre model_id (via model_key ou alias)."""
    # 1. model_key normalisé (source unique de vérité pour le nommage).
    key = model_key(name)
    if key:
        row = cat.conn.execute(
            "SELECT id FROM catalogue_models WHERE model_key = ? ORDER BY id LIMIT 1",
            (key,)).fetchone()
        if row:
            return row["id"], key
        # Fallback : ref exacte
        row = cat.conn.execute(
            "SELECT id FROM catalogue_models WHERE ref = ? LIMIT 1", (name,)).fetchone()
        if row:
            return row["id"], key
    # 2. Alias explicites (catalogue_aliases scope='model')
    try:
        row = cat.conn.execute(
            "SELECT canonical_ref FROM catalogue_aliases "
            "WHERE scope='model' AND alias = ?", (name,)).fetchone()
        if row:
            r2 = cat.conn.execute(
                "SELECT id FROM catalogue_models WHERE ref = ? OR model_key = ? LIMIT 1",
                (row["canonical_ref"], row["canonical_ref"])).fetchone()
            if r2:
                return r2["id"], key
    except Exception:
        pass
    return None, key


def build(cat, verbose: bool = False) -> dict:
    stats = {"provider": {"total": 0, "linked": 0},
             "benchmark": {"total": 0, "linked": 0}}

    def _insert(source, source_type, name, model_id, key):
        status = "linked" if model_id else "unresolved"
        if status == "linked":
            stats[source_type]["linked"] += 1
        stats[source_type]["total"] += 1
        try:
            cat.conn.execute("""
                INSERT OR REPLACE INTO alias_model
                    (model_id, source_name, source, source_type, confidence, status, updated_at)
                VALUES (?, ?, ?, ?, 'auto', ?, strftime('%s','now'))
            """, (model_id, name, source, source_type, status))
        except Exception as e:
            if verbose:
                print(f"  ⚠ {source}/{name}: {e}")

    # ── Source provider : provider_model_name par provider ──
    for r in cat.conn.execute("""
            SELECT p.ref AS provider, pm.provider_model_name AS name
            FROM provider_models pm
            JOIN catalogue_providers p ON p.id = pm.provider_id
        """):
        mid, key = _resolve_to_model_id(cat, r["name"])
        _insert(r["provider"], "provider", r["name"], mid, key)

    # ── Source benchmark : refs distinctes par benchmark_key ──
    for bk in BENCHMARK_SOURCES:
        rows = cat.conn.execute(
            "SELECT DISTINCT model_ref FROM model_benchmarks_raw WHERE benchmark_key = ?",
            (bk,)).fetchall()
        for r in rows:
            mid, key = _resolve_to_model_id(cat, r["model_ref"])
            _insert(bk, "benchmark", r["model_ref"], mid, key)

    cat.conn.commit()
    return stats


def report(cat) -> None:
    print("=== alias_model — résumé ===")
    for r in cat.conn.execute("""
            SELECT source, source_type, status, COUNT(*) n
            FROM alias_model GROUP BY source, source_type, status
            ORDER BY source_type, source, status
        """):
        print(f"  {r['source_type']:10s} {r['source']:24s} {r['status']:12s} {r['n']:6d}")
    print()
    print("=== Taux de résolution par source ===")
    for r in cat.conn.execute("""
            SELECT source, source_type,
                   COUNT(*) total,
                   SUM(status='linked') linked,
                   ROUND(100.0 * SUM(status='linked') / COUNT(*), 1) pct
            FROM alias_model GROUP BY source, source_type ORDER BY source_type, source
        """):
        print(f"  {r['source_type']:10s} {r['source']:24s} "
              f"{r['total']:6d} → {r['linked']:5d} ({r['pct']}%)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cat = CatalogueDB()
    if args.report:
        report(cat)
        return
    stats = build(cat, verbose=args.verbose)
    print("Alias construits:")
    print(f"  provider  : {stats['provider']['linked']}/{stats['provider']['total']} résolus")
    print(f"  benchmark : {stats['benchmark']['linked']}/{stats['benchmark']['total']} résolus")
    cat.close()


if __name__ == "__main__":
    main()
