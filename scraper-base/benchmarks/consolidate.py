#!/usr/bin/env python3
"""
ModelWeaver — Benchmark Scraper consolidator (v2, exhaustive).

Usage :
    python scraper-base/benchmarks/consolidate.py            → Turso distant
    python scraper-base/benchmarks/consolidate.py --local    → catalogue.db local
    python scraper-base/benchmarks/consolidate.py --local --force  → force re-scrape all

Pipeline :
    1. Connexion à la base (Turso ou locale).
    2. Création des tables si absentes.
    3. Scraping lazy : télécharge les CSVs LMSYS/Arena-Hard uniquement
       si plus récents que la dernière exécution.
       Génère des données synthétiques pour les modèles sans données réelles.
    4. Écriture dans model_benchmarks_raw (normalisé par source + métrique).
    5. Normalisation de chaque métrique en percentile relatif à l'ensemble
       complet des scores collectés pour cette métrique.
    6. Agrégation dans model_efficacy (score qualité/vitesse/coût/fiabilité).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ──────────────────────────────────────────────
#  1. CONFIG
# ──────────────────────────────────────────────

def _load_env(env_path: Optional[str] = None):
    if env_path:
        p = Path(env_path)
    else:
        p = Path(__file__).resolve().parent.parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())

_load_env()

TURSO_URL = os.getenv("TURSO_URL", "")
TURSO_TOKEN = os.getenv("TURSO_TOKEN", "")
MW_VERSION = os.getenv("MW_VERSION", "0.8.6")

SOURCES_DIR = Path(__file__).parent / "sources"
TRACKING_FILE = Path(__file__).parent / ".scrape_tracking.json"


# ──────────────────────────────────────────────
#  2. SCHEMA SQL
# ──────────────────────────────────────────────
SCHEMA_RAW_SQL = """
CREATE TABLE IF NOT EXISTS model_benchmarks_raw (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_ref       TEXT NOT NULL,
    benchmark_key   TEXT NOT NULL,
    metric_name     TEXT NOT NULL DEFAULT 'score',
    raw_value       REAL NOT NULL,
    percentile      REAL,
    source_url      TEXT DEFAULT '',
    fetched_at      TEXT DEFAULT (datetime('now')),
    is_synthetic    INTEGER DEFAULT 0,
    confidence      REAL DEFAULT 1.0,
    PRIMARY KEY (model_ref, benchmark_key, metric_name)
);
"""

SCHEMA_TRACKING_SQL = """
CREATE TABLE IF NOT EXISTS benchmark_scrape_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name     TEXT NOT NULL,
    source_url      TEXT DEFAULT '',
    rows_fetched    INTEGER DEFAULT 0,
    completed_at    TEXT DEFAULT (datetime('now')),
    success         INTEGER DEFAULT 1,
    error_msg       TEXT DEFAULT ''
);
"""


# ──────────────────────────────────────────────
#  3. NORMALIZATION
# ──────────────────────────────────────────────
def _percentile_rank(rows: List[Dict], metric_key: str = "raw_value") -> List[Dict]:
    """Assign percentile rank within each (benchmark_key, metric_name) group.

    Uses the standard percentile formula: pct = (rank / (n-1)) * 100
    """
    groups: Dict[str, List[Dict]] = {}
    for r in rows:
        key = f"{r['benchmark_key']}|{r['metric_name']}"
        groups.setdefault(key, []).append(r)

    for key, group in groups.items():
        vals = sorted(set(r[metric_key] for r in group))
        n = len(vals)
        for r in group:
            idx = vals.index(r[metric_key])
            r["percentile"] = round((idx / max(n - 1, 1)) * 100, 2)
    return rows


def normalize_all(rows: List[Dict]) -> List[Dict]:
    """Normalize every raw_value to percentile within its metric group.

    For cost metrics (cost_per_m_input, cost_per_m_output), invert so that
    lower cost = higher percentile (like quality & speed).
    """
    rows = _percentile_rank(rows, "raw_value")
    return rows


# ──────────────────────────────────────────────
#  4. CONSOLIDATION → model_efficacy
# ──────────────────────────────────────────────
def consolidate_to_efficacy(rows: List[Dict]) -> List[Dict]:
    """Aggregate percentile scores into per-model efficacy scores.

    Scoring logic:
        score_quality     = mean of MT-bench + MMLU + Arena-Hard percentiles
        score_speed       = mean of speed percentiles (higher = faster)
        score_cost        = mean of cost percentiles (higher = cheaper)
        score_reliability = mean of reliability percentiles + MMLU
        global_score      = weighted combination

    Each source carries confidence weight.
    """
    by_ref: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        ref = r["model_ref"]
        if ref not in by_ref:
            by_ref[ref] = {
                "q": [], "s": [], "c": [], "rl": [],
                "q_conf": [], "s_conf": [], "c_conf": [], "rl_conf": [],
                "sources": set(),
            }
        entry = by_ref[ref]
        conf = r.get("confidence", 1.0)

        metric = r["metric_name"]
        pct = r.get("percentile", 50.0)

        # Normalize cost: invert so cheaper = higher percentile
        if metric in ("cost_per_m_input", "cost_per_m_output"):
            pct = 100 - pct

# ──────────────────────────────────────────────
#  3. TASK-TYPE → METRIC MAPPING
# ──────────────────────────────────────────────
# Each benchmark metric maps to task types it measures.
# Scores are stored per-task-type per model in model_efficacy.
TASK_TYPE_METRICS = {
    "score_chat":        {"mt_bench_quality", "quality_pct"},
    "score_knowledge":   {"mmlu_knowledge", "quality_pct"},
    "score_coding":      {"score", "arena_hard_auto", "pass_rate"},
    "score_reasoning":   {"score", "arena_hard_auto", "pass_rate"},
    "score_agentic":     {"score", "arena_hard_auto"},
}


def _task_scores_from_metrics(
    metrics_by_type: Dict[str, List[float]],
    confidences_by_type: Dict[str, List[float]],
) -> Dict[str, float]:
    """metrics_by_type is already grouped by task type (score_chat, etc).
    Compute weighted average per task type."""
    scores = {}
    for task, vals in metrics_by_type.items():
        confs = confidences_by_type.get(task, [])
        if vals:
            total_w = sum(confs)
            if total_w > 0:
                scores[task] = round(sum(v * c for v, c in zip(vals, confs)) / total_w, 2)
            else:
                scores[task] = round(statistics.mean(vals), 2)
        else:
            scores[task] = 0.0
    return scores


def consolidate_to_efficacy(rows: List[Dict]) -> List[Dict]:
    """Aggregate percentile scores into per-model efficacy scores with task breakdown.

    Each model gets:
      - global_score: weighted combination of all scores
      - score_quality / score_speed / score_cost / score_reliability (existing)
      - score_chat / score_knowledge / score_coding / score_reasoning / score_agentic (new)
      - is_synthetic: True only if ALL data is from synthetic sources
    """
    by_ref: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        ref = r["model_ref"]
        if ref not in by_ref:
            by_ref[ref] = {
                "q": [], "s": [], "c": [], "rl": [],
                "q_conf": [], "s_conf": [], "c_conf": [], "rl_conf": [],
                "task_metrics": {},   # task_type → list of percentile values
                "task_conf": {},      # task_type → list of confidences
                "sources": set(),
            }
        entry = by_ref[ref]
        conf = r.get("confidence", 1.0)

        metric = r["metric_name"]
        pct = r.get("percentile", 50.0)

        # Normalize cost: invert so cheaper = higher percentile
        if metric in ("cost_per_m_input", "cost_per_m_output"):
            pct = 100 - pct

        # Aggregate by task type
        for task, metrics in TASK_TYPE_METRICS.items():
            if metric in metrics:
                entry["task_metrics"].setdefault(task, []).append(pct)
                entry["task_conf"].setdefault(task, []).append(conf)

        # Legacy aggregation for global score
        if metric in ("mt_bench_quality", "mmlu_knowledge", "score", "quality_pct"):
            entry["q"].append(pct)
            entry["q_conf"].append(conf)
            entry["sources"].add(r["benchmark_key"])
        elif metric in ("speed_tps",):
            entry["s"].append(pct)
            entry["s_conf"].append(conf)
            entry["sources"].add(r["benchmark_key"])
        elif metric in ("cost_per_m_input", "cost_per_m_output"):
            entry["c"].append(pct)
            entry["c_conf"].append(conf)
            entry["sources"].add(r["benchmark_key"])
        elif metric in ("reliability_pct", "mmlu_knowledge", "pass_rate"):
            entry["rl"].append(pct)
            entry["rl_conf"].append(conf)
            entry["sources"].add(r["benchmark_key"])

    results = []
    for ref, sc in by_ref.items():
        def _weighted_mean(values, confidences):
            if not values:
                return 0.0
            total_w = sum(confidences)
            if total_w == 0:
                return statistics.mean(values)
            return round(sum(v * c for v, c in zip(values, confidences)) / total_w, 2)

        q = _weighted_mean(sc["q"], sc["q_conf"])
        s = _weighted_mean(sc["s"], sc["s_conf"])
        c = _weighted_mean(sc["c"], sc["c_conf"])
        rl = _weighted_mean(sc["rl"], sc["rl_conf"])

        # Compute per-task-type scores
        task_scores = _task_scores_from_metrics(sc["task_metrics"], sc["task_conf"])

        n_data = len(sc["q"]) + len(sc["s"]) + len(sc["c"]) + len(sc["rl"])
        source_count = len(sc["sources"])
        _REAL_SOURCES = {"lmsys_arena", "arena_hard_auto", "artificial_analysis", "seeded"}
        has_real = sc["sources"].intersection(_REAL_SOURCES)
        is_synth = 0 if has_real else 1

        # Compute task-type weighted global score
        task_global = round(
            task_scores.get("score_chat", 0) * 0.35
            + task_scores.get("score_coding", 0) * 0.20
            + task_scores.get("score_reasoning", 0) * 0.20
            + task_scores.get("score_knowledge", 0) * 0.15
            + task_scores.get("score_agentic", 0) * 0.10,
            2,
        )

        # Compute legacy weighted global score
        legacy_global = round(q * 0.35 + s * 0.20 + c * 0.15 + rl * 0.30, 2)

        # Prefer task-type global when available (covers frontier models
        # with score_coding/score_reasoning etc. that have no speed/cost/reliability)
        has_task_data = any(task_scores.values())
        if has_task_data:
            global_score = task_global
        else:
            global_score = legacy_global

        results.append({
            "model_ref": ref,
            "global_score": global_score,
            "score_quality": q,
            "score_speed": s,
            "score_cost": c,
            "score_reliability": rl,
            **task_scores,
            "samples": n_data,
            "source_count": source_count,
            "is_synthetic": is_synth,
            "benchmark_keys": list(sc["sources"]),
        })

    return results


# ──────────────────────────────────────────────
#  5. DATABASE CONNECTION
# ──────────────────────────────────────────────
def get_local_connection() -> sqlite3.Connection:
    home = Path(os.environ.get("MODELWEAVER_HOME", Path.home() / ".modelweaver"))
    db_path = home / "catalogue.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def get_turso_connection():
    if not TURSO_URL or not TURSO_TOKEN:
        raise RuntimeError(
            "TURSO_URL + TURSO_TOKEN non définis.\n"
            "  Utilisez --local pour travailler en local.\n"
        )
    import libsql
    return libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)


# ──────────────────────────────────────────────
#  6. SCHEMA — ensure tables exist (local + Turso)
# ──────────────────────────────────────────────
def ensure_schema(conn: sqlite3.Connection, local: bool):
    conn.execute(SCHEMA_RAW_SQL)
    conn.execute(SCHEMA_TRACKING_SQL)
    conn.commit()
    # Add missing columns if absent (self-healing schema)
    _add_column(conn, "model_benchmarks_raw", "is_synthetic", "INTEGER DEFAULT 0")
    _add_column(conn, "model_benchmarks_raw", "confidence", "REAL DEFAULT 1.0")
    _add_column(conn, "model_efficacy", "model_ref", "TEXT")
    _add_column(conn, "model_efficacy", "source_count", "INTEGER DEFAULT 0")
    _add_column(conn, "model_efficacy", "is_synthetic", "INTEGER DEFAULT 0")
    _add_column(conn, "model_efficacy", "benchmark_keys", "TEXT DEFAULT '[]'")
    _add_column(conn, "model_efficacy", "score_chat", "REAL DEFAULT 0")
    _add_column(conn, "model_efficacy", "score_knowledge", "REAL DEFAULT 0")
    _add_column(conn, "model_efficacy", "score_coding", "REAL DEFAULT 0")
    _add_column(conn, "model_efficacy", "score_reasoning", "REAL DEFAULT 0")
    _add_column(conn, "model_efficacy", "score_agentic", "REAL DEFAULT 0")
    _add_column(conn, "model_efficacy", "global_score", "REAL DEFAULT 0")


def _add_column(conn: sqlite3.Connection, table: str, column: str, col_type: str):
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()
    except Exception:
        pass


# ──────────────────────────────────────────────
#  7. SCRAPE TRACKING — lazy/incremental
# ──────────────────────────────────────────────
def _load_tracking() -> dict:
    if TRACKING_FILE.exists():
        try:
            return json.loads(TRACKING_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_tracking(tracking: dict):
    TRACKING_FILE.write_text(json.dumps(tracking, indent=2))


def _should_scrape(source_key: str, url: str, tracking: dict) -> Tuple[bool, Optional[str]]:
    """Return (force_scrape, latest_date) based on tracking data."""
    entry = tracking.get(source_key, {})
    last_run = entry.get("last_run", "")
    last_csv_date = entry.get("csv_date", "")

    # Check if remote CSV has a newer date
    try:
        current_date = _extract_csv_date(url)
        if current_date and last_csv_date and current_date > last_csv_date:
            return True, current_date
        if not last_csv_date and current_date:
            return True, current_date
    except Exception:
        pass

    # If no tracking, scrape
    if not entry:
        return True, None

    # Force if --force flag was used (tracked separately)
    if tracking.get("_force", False):
        return True, None

    return False, None


def _extract_csv_date(url: str) -> Optional[str]:
    m = re.search(r"leaderboard_table_(\d{8})\.csv", url)
    return m.group(1) if m else None


def _record_scrape(tracking: dict, source_name: str, url: str,
                    rows_fetched: int, success: bool, error_msg: str = ""):
    tracking[source_name] = {
        "last_run": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "url": url,
        "rows_fetched": rows_fetched,
        "success": success,
        "csv_date": _extract_csv_date(url) or "",
    }
    if not success:
        tracking[source_name]["last_error"] = error_msg
    tracking["_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")


# ──────────────────────────────────────────────
#  8. WRITE HELPERS
# ──────────────────────────────────────────────
def _resolve_model_id(conn: sqlite3.Connection, model_ref: str) -> Optional[int]:
    row = conn.execute("SELECT id FROM catalogue_models WHERE ref = ?", (model_ref,)).fetchone()
    if row:
        return row["id"] if isinstance(row, sqlite3.Row) else row[0]
    short = model_ref.split("/", 1)[-1] if "/" in model_ref else model_ref
    row = conn.execute("SELECT id FROM catalogue_models WHERE ref LIKE ?", (f"%{short}%",)).fetchone()
    if row:
        return row["id"] if isinstance(row, sqlite3.Row) else row[0]
    return None


def _model_name_to_ref(conn: sqlite3.Connection, name: str) -> Optional[str]:
    """Try to resolve a model name from a CSV to our canonical ref."""
    short = name.lower().strip()
    # Direct match on ref
    row = conn.execute("SELECT ref FROM catalogue_models WHERE LOWER(ref) = ?", (short,)).fetchone()
    if row:
        return row["ref"] if isinstance(row, sqlite3.Row) else row[0]
    # Fuzzy: ref contains the name or vice versa
    row = conn.execute("SELECT ref FROM catalogue_models WHERE LOWER(ref) LIKE ?", (f"%{short}%",)).fetchone()
    if row:
        return row["ref"] if isinstance(row, sqlite3.Row) else row[0]
    # Extract just the model part (after /)
    if "/" in short:
        short = short.split("/", 1)[1]
    row = conn.execute("SELECT ref FROM catalogue_models WHERE LOWER(ref) LIKE ?", (f"%{short}%",)).fetchone()
    if row:
        return row["ref"] if isinstance(row, sqlite3.Row) else row[0]
    return None


def write_raw(conn: sqlite3.Connection, rows: List[Dict]):
    count = 0
    skipped = 0
    for r in rows:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO model_benchmarks_raw "
                "(model_ref, benchmark_key, metric_name, raw_value, percentile, "
                "source_url, fetched_at, is_synthetic, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)",
                (r["model_ref"], r["benchmark_key"], r["metric_name"],
                 r["raw_value"], r.get("percentile", 50.0),
                 r.get("source_url", ""),
                 r.get("is_synthetic", 0),
                 r.get("confidence", 1.0)),
            )
            count += 1
        except Exception as e:
            skipped += 1
            if skipped <= 5:
                print(f"    ⚠ skip {r.get('model_ref','?')}: {e}")
    conn.commit()
    print(f"    → {count} écrits, {skipped} skipped")


TASK_TYPE_COLS = "score_chat REAL DEFAULT 0, score_knowledge REAL DEFAULT 0, score_coding REAL DEFAULT 0, score_reasoning REAL DEFAULT 0, score_agentic REAL DEFAULT 0"

def write_efficacy(conn: sqlite3.Connection, rows: List[Dict], local: bool):
    written = 0
    for r in rows:
        try:
            if local:
                model_id = _resolve_model_id(conn, r["model_ref"])
                if not model_id:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO model_efficacy "
                    "(model_id, model_ref, use_case, score_quality, score_speed, "
                    "score_cost, score_reliability, samples, "
                    "source_count, is_synthetic, benchmark_keys, "
                    "score_chat, score_knowledge, score_coding, score_reasoning, score_agentic, global_score) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (model_id, r["model_ref"], "general",
                     r["score_quality"], r["score_speed"], r["score_cost"],
                     r["score_reliability"], r.get("samples", 0),
                     r.get("source_count", 0), r.get("is_synthetic", 0),
                     json.dumps(r.get("benchmark_keys", [])),
                     r.get("score_chat", 0), r.get("score_knowledge", 0),
                     r.get("score_coding", 0), r.get("score_reasoning", 0),
                     r.get("score_agentic", 0), r.get("global_score", 0)),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO model_efficacy "
                    "(model_ref, use_case, score_quality, score_speed, "
                    "score_cost, score_reliability, samples, "
                    "source_count, is_synthetic, benchmark_keys, "
                    "score_chat, score_knowledge, score_coding, score_reasoning, score_agentic, global_score) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["model_ref"], "general",
                     r["score_quality"], r["score_speed"], r["score_cost"],
                     r["score_reliability"], r.get("samples", 0),
                     r.get("source_count", 0), r.get("is_synthetic", 0),
                     json.dumps(r.get("benchmark_keys", [])),
                     r.get("score_chat", 0), r.get("score_knowledge", 0),
                     r.get("score_coding", 0), r.get("score_reasoning", 0),
                     r.get("score_agentic", 0), r.get("global_score", 0)),
                )
            written += 1
        except Exception as e:
            print(f"    ⚠ efficacy {r.get('model_ref','?')}: {e}")
    conn.commit()
    print(f"    → {written}/{len(rows)} écrits") 


# ──────────────────────────────────────────────
#  9. SCRAPE TRACKING LOG
# ──────────────────────────────────────────────
def append_scrape_log(conn: sqlite3.Connection, source_name: str, url: str,
                        rows_fetched: int, success: bool, error_msg: str = ""):
    try:
        conn.execute(
            "INSERT INTO benchmark_scrape_log (source_name, source_url, rows_fetched, success, error_msg) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_name, url, rows_fetched, 1 if success else 0, error_msg),
        )
        conn.commit()
    except Exception:
        pass


# ──────────────────────────────────────────────
#  10. MAIN
# ──────────────────────────────────────────────
def main():
    local = "--local" in sys.argv
    force = "--force" in sys.argv

    print("=" * 60)
    print("ModelWeaver — Benchmark Scraper v2")
    print(f"Cible: {'📁 catalogue.db local' if local else '☁️  Turso distant'}")
    print(f"Mode: {'FORCE re-scrape' if force else 'lazy (incrémental)'}")
    print("=" * 60)

    # 1. Connexion
    try:
        conn = get_local_connection() if local else get_turso_connection()
        print("\n✅ Connexion établie")
    except Exception as e:
        print(f"\n❌ {e}")
        sys.exit(1)

    # 2. Schema
    ensure_schema(conn, local)
    print("✅ Schéma OK")

    # 3. Load tracking
    tracking = _load_tracking()
    if force:
        tracking["_force"] = True

    # 4. Scraping
    sys.path.insert(0, str(SOURCES_DIR))
    from sources import fetch_all

    local_db = str(Path.home() / ".modelweaver" / "catalogue.db") if local else ":memory:"
    source_results = fetch_all(local_db)

    all_raw = []
    for src_name, rows in source_results.items():
        print(f"    {src_name}: {len(rows)} lignes")
        all_raw.extend(rows)

    if not all_raw:
        print("  ❌ Aucune donnée récupérée.")
        sys.exit(1)

    print(f"\n  Total: {len(all_raw)} lignes brutes")

    # 5. Normalize percentiles
    print("\n  Normalisation percentiles...")
    all_raw = normalize_all(all_raw)

    # 6. Write raw
    print("\n  Écriture model_benchmarks_raw...")
    write_raw(conn, all_raw)
    append_scrape_log(conn, "all_sources", "", len(all_raw), True)
    print(f"✅ model_benchmarks_raw: {len(all_raw)} lignes")

    # 7. Consolidate
    print("\n  Consolidation model_efficacy...")
    efficacy = consolidate_to_efficacy(all_raw)
    print(f"  {len(efficacy)} modèles scorés "
          f"({sum(1 for r in efficacy if r['is_synthetic'])} synthétiques)")

    # 8. Write efficacy
    write_efficacy(conn, efficacy, local)
    print(f"✅ model_efficacy: {len(efficacy)} modèles")

    # 9. Top 10
    top = sorted(efficacy, key=lambda r: r["global_score"], reverse=True)[:10]
    print(f"\n  Top 10 (score global):")
    for m in top:
        synth = " 🧪" if m["is_synthetic"] else ""
        print(f"    {m['model_ref']:40s} {m['global_score']:6.2f}  "
              f"Q={m['score_quality']:6.2f}  S={m['score_speed']:6.2f}  "
              f"C={m['score_cost']:6.2f}  R={m['score_reliability']:6.2f}"
              f"  [{m['source_count']}src]{synth}")

    # 10. Stats
    real = [r for r in efficacy if not r["is_synthetic"]]
    synth = [r for r in efficacy if r["is_synthetic"]]
    print(f"\n  Stats: {len(real)} réels + {len(synth)} synthétiques = {len(efficacy)} total")

    conn.close()
    print(f"\n{'=' * 60}")
    print("Terminé.")


if __name__ == "__main__":
    main()