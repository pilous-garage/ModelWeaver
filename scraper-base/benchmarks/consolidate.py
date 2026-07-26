#!/usr/bin/env python3
"""
Scraper de benchmarks LLM — script autonome.

Usage :
    python scraper-base/benchmarks/consolidate.py           # → Turso distant
    python scraper-base/benchmarks/consolidate.py --local   # → catalogue.db local

Pipeline :
    1. Connexion à la base (Turso ou locale)
    2. Création des tables si absentes
    3. Scraping des sources (seeded → LMSYS → Artificial Analysis)
    4. Normalisation en percentiles
    5. Écriture dans model_benchmarks_raw
    6. Consolidation dans model_efficacy
"""

import json
import os
import re
import sqlite3
import statistics
import sys
import urllib.request
import urllib.error
import ssl
from pathlib import Path
from typing import Any, Dict, List, Optional

# ──────────────────────────────────────────────
#  1. CONFIG — .env
# ──────────────────────────────────────────────

def _load_env(env_path: Optional[str] = None):
    """Charge .env depuis le repo root ou le path donné."""
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

# ──────────────────────────────────────────────
#  2. SCHEMA SQL
# ──────────────────────────────────────────────

SCHEMA_RAW_SQL = """
CREATE TABLE IF NOT EXISTS model_benchmarks_raw (
    model_ref       TEXT NOT NULL,
    benchmark_key   TEXT NOT NULL,
    metric_name     TEXT NOT NULL DEFAULT 'score',
    raw_value       REAL NOT NULL,
    percentile      REAL,
    source_url      TEXT DEFAULT '',
    fetched_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (model_ref, benchmark_key, metric_name)
);
"""

# ──────────────────────────────────────────────
#  3. SEEDED DATA (20 modèles)
# ──────────────────────────────────────────────

# (model_ref, elo, quality%, speed_tps, cost_per_m_input, cost_per_m_output, swe_bench_pass_rate)
SEED = [
    ("openai/gpt-4o",               1365, 92,  85,  2.50,  10.00, 48.0),
    ("openai/gpt-4o-mini",          1320, 85, 120,  0.15,   0.60, 32.0),
    ("openai/o1",                   1380, 95,  30, 15.00,  60.00, 62.0),
    ("openai/o3-mini",              1340, 90,  55,  1.10,   4.40, 55.0),
    ("anthropic/claude-3-5-sonnet", 1370, 93,  55,  3.00,  15.00, 50.0),
    ("anthropic/claude-3-5-haiku",  1310, 83,  95,  0.80,   4.00, 35.0),
    ("anthropic/claude-4-sonnet",   1390, 96,  50,  3.50,  17.50, 58.0),
    ("google/gemini-2.0-flash",     1330, 87, 110,  0.10,   0.40, 30.0),
    ("google/gemini-2.5-flash",     1345, 89, 100,  0.15,   0.60, 36.0),
    ("google/gemini-2.0-pro",       1350, 91,  60,  0.50,   1.50, 42.0),
    ("meta/llama-3.1-70b",          1280, 80,  45,  0.59,   0.79, 28.0),
    ("meta/llama-3.1-405b",         1300, 85,  30,  2.00,   2.00, 35.0),
    ("meta/llama-4-70b",            1315, 86,  50,  0.70,   0.90, 38.0),
    ("mistral/mistral-large",       1290, 82,  70,  2.00,   6.00, 30.0),
    ("mistral/mistral-small",       1260, 76,  90,  0.20,   0.60, 22.0),
    ("deepseek/deepseek-chat",      1340, 88,  75,  0.14,   0.28, 42.0),
    ("deepseek/deepseek-reasoner",  1355, 91,  40,  0.55,   2.19, 48.0),
    ("cohere/command-r-plus",       1240, 72,  65,  2.50,  10.00, 18.0),
    ("qwen/qwen3-72b",              1310, 84,  55,  0.35,   0.70, 34.0),
    ("qwen/qwen3-32b",              1295, 81,  70,  0.20,   0.40, 28.0),
]


def _fetch_seeded() -> List[Dict[str, Any]]:
    rows = []
    for ref, elo, quality, speed, cost_in, cost_out, swe in SEED:
        rows.append({"model_ref": ref, "benchmark_key": "lmsys_arena_elo",
                     "metric_name": "elo", "raw_value": elo, "source_url": "seeded/lmsys"})
        rows.append({"model_ref": ref, "benchmark_key": "artificial_analysis",
                     "metric_name": "quality", "raw_value": quality, "source_url": "seeded/artificial-analysis"})
        rows.append({"model_ref": ref, "benchmark_key": "artificial_analysis",
                     "metric_name": "speed_tps", "raw_value": speed, "source_url": "seeded/artificial-analysis"})
        rows.append({"model_ref": ref, "benchmark_key": "artificial_analysis",
                     "metric_name": "cost_per_m_input", "raw_value": cost_in, "source_url": "seeded/artificial-analysis"})
        rows.append({"model_ref": ref, "benchmark_key": "artificial_analysis",
                     "metric_name": "cost_per_m_output", "raw_value": cost_out, "source_url": "seeded/artificial-analysis"})
        rows.append({"model_ref": ref, "benchmark_key": "swe_bench_verified",
                     "metric_name": "pass_rate", "raw_value": swe, "source_url": "seeded/swe-bench"})
    return rows

# ──────────────────────────────────────────────
#  4. LIVE SCRAPERS (LMSYS Arena, Artificial Analysis)
# ──────────────────────────────────────────────

LMSYS_URL = "https://huggingface.co/spaces/lmarena-ai/arena-leaderboard/raw/main/leaderboard_table.json"
AA_URL = "https://artificialanalysis.ai/api/models"

_PROVIDER_MAP = [
    ("gpt-", "openai/gpt-"), ("o1-", "openai/o1-"), ("o3-", "openai/o3-"),
    ("claude", "anthropic/claude"), ("gemini", "google/gemini"), ("gemma", "google/gemma"),
    ("meta-llama", "meta/llama"), ("llama", "meta/llama"),
    ("mistral", "mistral/mistral"), ("mixtral", "mistral/mixtral"),
    ("deepseek", "deepseek/deepseek"), ("qwen", "qwen/qwen"),
    ("command", "cohere/command"), ("dbrx", "databricks/dbrx"),
]


def _norm_model(name: str) -> str:
    n = name.strip().lower()
    for prefix, replacement in _PROVIDER_MAP:
        if n.startswith(prefix):
            n = replacement + n[len(prefix):]
            break
    # Nettoyer suffixes date
    n = re.sub(r'-\d{8}$', '', n)             # -YYYYMMDD
    n = re.sub(r'-\d{4}(-\d{2}(-\d{2})?)?$', '', n)  # -YYYY, -YYYY-MM, -YYYY-MM-DD
    n = re.sub(r'-v\d+$', '', n)
    n = re.sub(r'-(exp|beta|latest|turbo|snapshot)$', '', n)
    return n


def _fetch_lmsys() -> List[Dict[str, Any]]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(LMSYS_URL, headers={"User-Agent": "ModelWeaver/0.8.5"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ⚠ LMSYS non dispo ({e})")
        return []
    items = data if isinstance(data, list) else data.get("models", data.get("data", []))
    rows, seen = [], set()
    for item in items:
        if isinstance(item, dict):
            name = item.get("name", item.get("model", ""))
            elo = item.get("elo", item.get("score", 0))
        elif isinstance(item, list) and len(item) >= 2:
            name, elo = item[0], item[1]
        else:
            continue
        if not name or not elo:
            continue
        ref = _norm_model(str(name))
        if not ref or ref in seen:
            continue
        seen.add(ref)
        rows.append({"model_ref": ref, "benchmark_key": "lmsys_arena_elo",
                     "metric_name": "elo", "raw_value": float(elo), "source_url": LMSYS_URL})
    return rows


def _fetch_artificial_analysis() -> List[Dict[str, Any]]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(AA_URL, headers={"User-Agent": "ModelWeaver/0.8.5",
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ⚠ Artificial Analysis non dispo ({e})")
        return []
    models = data if isinstance(data, list) else data.get("models", data.get("data", []))
    rows, seen = [], set()
    for item in models:
        if not isinstance(item, dict):
            continue
        name = item.get("name", item.get("model", ""))
        if not name:
            continue
        ref = _norm_model(str(name))
        if not ref or ref in seen:
            continue
        seen.add(ref)
        for metric_key, metric_name, raw_key in [
            ("artificial_analysis", "quality", "quality"),
            ("artificial_analysis", "speed_tps", "speed"),
            ("artificial_analysis", "cost_per_m_input", "price_per_million_input_tokens"),
            ("artificial_analysis", "cost_per_m_output", "price_per_million_output_tokens"),
        ]:
            val = item.get(raw_key, item.get(metric_name))
            if val is not None:
                rows.append({"model_ref": ref, "benchmark_key": metric_key,
                             "metric_name": metric_name, "raw_value": float(val),
                             "source_url": AA_URL})
    return rows


def fetch_all_sources() -> List[Dict[str, Any]]:
    """Scrape toutes les sources et retourne la liste consolidée des lignes brutes."""
    all_rows = []
    print("\n  Scraping sources...")
    for name, fn in [("seeded", _fetch_seeded), ("LMSYS Arena", _fetch_lmsys),
                      ("Artificial Analysis", _fetch_artificial_analysis)]:
        rows = fn()
        print(f"    {name}: {len(rows)} lignes")
        all_rows.extend(rows)
    return all_rows

# ──────────────────────────────────────────────
#  5. PERCENTILES
# ──────────────────────────────────────────────

def compute_percentiles(rows: List[Dict]) -> List[Dict]:
    groups: Dict[str, List[Dict]] = {}
    for r in rows:
        groups.setdefault(f"{r['benchmark_key']}|{r['metric_name']}", []).append(r)
    for key, group in groups.items():
        vals = sorted(set(r["raw_value"] for r in group))
        for r in group:
            r["percentile"] = round((vals.index(r["raw_value"]) / max(len(vals) - 1, 1)) * 100, 1)
    return rows

# ──────────────────────────────────────────────
#  6. CONSOLIDATION → model_efficacy
# ──────────────────────────────────────────────

def consolidate_to_efficacy(rows: List[Dict]) -> List[Dict]:
    by_ref: Dict[str, Dict] = {}
    for r in rows:
        ref = r["model_ref"]
        by_ref.setdefault(ref, {"q": [], "s": [], "c": []})
        m, pct = r["metric_name"], r.get("percentile", 50)
        if m in ("elo", "quality"):
            by_ref[ref]["q"].append(pct)
        elif m in ("speed_tps",):
            by_ref[ref]["s"].append(pct)
        elif m in ("cost_per_m_input", "cost_per_m_output"):
            by_ref[ref]["c"].append(100 - pct)
    results = []
    for ref, sc in by_ref.items():
        q = round(statistics.mean(sc["q"]), 1) if sc["q"] else 0.0
        s = round(statistics.mean(sc["s"]), 1) if sc["s"] else 0.0
        c = round(statistics.mean(sc["c"]), 1) if sc["c"] else 50.0
        samples = len(sc["q"]) + len(sc["s"]) + len(sc["c"])
        global_ = round(q * 0.4 + s * 0.2 + c * 0.2 + 0.5 * 0.2, 1)
        results.append({"model_ref": ref, "global_score": global_,
                        "score_quality": q, "score_speed": s, "score_cost": c,
                        "score_reliability": 50.0, "samples": samples})
    return results

# ──────────────────────────────────────────────
#  7. CONNEXION BASE
# ──────────────────────────────────────────────

def get_connection(local: bool):
    if local:
        return _get_local_conn()
    return _get_turso_conn()


def _get_local_conn() -> sqlite3.Connection:
    home = Path(os.environ.get("MODELWEAVER_HOME", Path.home() / ".modelweaver"))
    db_path = home / "catalogue.db"
    if not db_path.exists():
        print(f"  Création base locale: {db_path}")
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)  # 10s busy timeout
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _get_turso_conn():
    if not TURSO_URL or not TURSO_TOKEN:
        raise RuntimeError(
            "TURSO_URL et TURSO_TOKEN non définis.\n"
            "  Soit dans .env à la racine du projet,\n"
            "  Soit en variables d'environnement.\n"
            "Utilise --local pour la base catalogue locale."
        )
    import libsql
    conn = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
    return conn

# ──────────────────────────────────────────────
#  8. ÉCRITURE
# ──────────────────────────────────────────────

def ensure_schema(conn, local: bool):
    print("  Création des tables si absentes...")
    # model_benchmarks_raw (nouvelle table, n'existe nulle part)
    for attempt in range(3):
        try:
            conn.execute(SCHEMA_RAW_SQL)
            conn.commit()
            break
        except Exception as e:
            if attempt < 2:
                import time; time.sleep(0.5)
            else:
                print(f"    ⚠ model_benchmarks_raw: {e}")
    # model_efficacy existe déjà dans le schéma catalogue.
    # En local : utilise la table existante (model_id FK, pas model_ref).
    # Sur Turso : ajoute model_ref si pas déjà là.
    if not local:
        try:
            conn.execute("ALTER TABLE model_efficacy ADD COLUMN model_ref TEXT")
            conn.commit()
        except Exception:
            pass  # existe déjà ou pas possible


def write_raw(conn, rows: List[Dict]):
    print(f"  Écriture de {len(rows)} lignes dans model_benchmarks_raw...")
    for r in rows:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO model_benchmarks_raw "
                "(model_ref, benchmark_key, metric_name, raw_value, percentile, source_url) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r["model_ref"], r["benchmark_key"], r["metric_name"],
                 r["raw_value"], r.get("percentile"), r.get("source_url", ""))
            )
        except Exception as e:
            print(f"    ⚠ {r['model_ref']}/{r['benchmark_key']}: {e}")
    conn.commit()


def _resolve_model_id(conn, model_ref: str) -> Optional[int]:
    """Trouve catalogue_models.id pour un model_ref avec fallback."""
    # 1. Match exact
    row = conn.execute("SELECT id FROM catalogue_models WHERE ref = ?", (model_ref,)).fetchone()
    if row:
        return row["id"] if isinstance(row, sqlite3.Row) else row[0]
    # 2. Match sur le nom après le slash (ex: 'openai/gpt-4o' → 'gpt-4o')
    short = model_ref.split("/", 1)[-1] if "/" in model_ref else model_ref
    row = conn.execute("SELECT id FROM catalogue_models WHERE ref LIKE ?", (f"%{short}%",)).fetchone()
    if row:
        return row["id"] if isinstance(row, sqlite3.Row) else row[0]
    # 3. Match insensible à la casse
    row = conn.execute("SELECT id FROM catalogue_models WHERE LOWER(ref) = ?", (model_ref.lower(),)).fetchone()
    if row:
        return row["id"] if isinstance(row, sqlite3.Row) else row[0]
    return None


def write_efficacy(conn, rows: List[Dict], local: bool):
    print(f"  Écriture de {len(rows)} modèles dans model_efficacy...")
    written = 0
    for r in rows:
        try:
            if local:
                model_id = _resolve_model_id(conn, r["model_ref"])
                if not model_id:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO model_efficacy "
                    "(model_id, use_case, score_quality, score_speed, "
                    " score_cost, score_reliability, samples) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (model_id, "general",
                     r["score_quality"], r["score_speed"], r["score_cost"],
                     r["score_reliability"], r.get("samples", 1))
                )
                written += 1
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO model_efficacy "
                    "(model_ref, use_case, score_quality, score_speed, "
                    " score_cost, score_reliability, samples) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (r["model_ref"], "general",
                     r["score_quality"], r["score_speed"], r["score_cost"],
                     r["score_reliability"], r.get("samples", 1))
                )
                written += 1
        except Exception as e:
            print(f"    ⚠ {r['model_ref']}: {e}")
    conn.commit()
    print(f"    → {written}/{len(rows)} écrits")

# ──────────────────────────────────────────────
#  9. MAIN
# ──────────────────────────────────────────────

def main():
    local = "--local" in sys.argv

    print("=" * 60)
    print("ModelWeaver — LLM Benchmark Scraper")
    print(f"Cible: {'📁 catalogue.db local' if local else '☁️  Turso distant'}")
    print("=" * 60)

    # 1. Connexion
    try:
        conn = get_connection(local)
        print(f"\n✅ Connexion établie")
    except Exception as e:
        print(f"\n❌ {e}")
        sys.exit(1)

    # 2. Schema
    try:
        ensure_schema(conn, local)
        print(f"✅ Schéma OK")
    except Exception as e:
        print(f"\n❌ Erreur schéma: {e}")
        sys.exit(1)

    # 3. Scraping
    all_raw = fetch_all_sources()
    if not all_raw:
        print("  Aucune donnée récupérée.")
        sys.exit(1)

    # 4. Percentiles
    print("\n  Calcul des percentiles...")
    all_raw = compute_percentiles(all_raw)

    # 5. Écriture raw
    try:
        write_raw(conn, all_raw)
        print(f"✅ model_benchmarks_raw: {len(all_raw)} lignes")
    except Exception as e:
        print(f"❌ Écriture raw: {e}")
        sys.exit(1)

    # 6. Consolidation
    print("\n  Consolidation model_efficacy...")
    efficacy = consolidate_to_efficacy(all_raw)
    print(f"  {len(efficacy)} modèles scorés")

    # 7. Écriture efficacy
    try:
        write_efficacy(conn, efficacy, local)
        print(f"✅ model_efficacy: {len(efficacy)} modèles")

        # Top 5
        top = sorted(efficacy, key=lambda r: r["global_score"], reverse=True)[:5]
        print(f"\n  Top 5 modèles (score global):")
        for m in top:
            print(f"    {m['model_ref']:35s} {m['global_score']:5.1f}  "
                  f"Q={m['score_quality']:5.1f}  S={m['score_speed']:5.1f}  "
                  f"C={m['score_cost']:5.1f}")
    except Exception as e:
        print(f"❌ Écriture efficacy: {e}")
        sys.exit(1)

    # 8. Fermeture
    try:
        conn.close()
    except Exception:
        pass

    print(f"\n{'=' * 60}")
    print("Terminé.")


if __name__ == "__main__":
    main()
