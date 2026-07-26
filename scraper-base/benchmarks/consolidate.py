"""Consolidation des scores bruts → model_efficacy.

Pipeline :
    1. Lire model_benchmarks_raw (toutes sources)
    2. Grouper par (model_ref, use_case implicite)
    3. Calculer les scores composites (moyenne des percentiles)
    4. Upsert dans model_efficacy sur Turso

Les percentiles sont calculés par benchmark_key : chaque modèle
reçoit un rang percentile 0-100 par rapport aux autres modèles
de la même source.
"""

from __future__ import annotations
import statistics
from typing import Any, Dict, List, Optional


def compute_percentiles(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ajoute un percentile (0-100) à chaque ligne, par benchmark_key + metric_name.

    Le percentile = rang du modèle dans la distribution.
    100 = meilleur score, 0 = pire score.
    """
    # Grouper par (benchmark_key, metric_name)
    groups: Dict[str, List[Dict]] = {}
    for row in rows:
        key = f"{row['benchmark_key']}|{row['metric_name']}"
        groups.setdefault(key, []).append(row)

    for key, group in groups.items():
        values = sorted([r["raw_value"] for r in group])
        n = len(values)
        for row in group:
            if n <= 1:
                row["percentile"] = 50.0
            else:
                rank = values.index(row["raw_value"])
                row["percentile"] = round((rank / (n - 1)) * 100, 1)

    return rows


def consolidate_to_efficacy(all_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Consolide les lignes brutes (avec percentiles) en lignes model_efficacy.

    Regroupe par model_ref et calcule :
        score_quality   = moyenne percentiles des benchmarks de qualité
        score_speed     = moyenne percentiles des métriques de vitesse
        score_cost      = 1 - percentile coût (plus cher = moins bon)
        score_reliability = (pas encore, défaut 0.5)
    """
    # Grouper par model_ref
    by_model: Dict[str, Dict] = {}
    for row in all_rows:
        ref = row["model_ref"]
        if ref not in by_model:
            by_model[ref] = {"quality": [], "speed": [], "cost": []}
        m = row["metric_name"]
        pct = row.get("percentile", 50.0)

        # Qualité
        if m in ("elo", "quality", "overall"):
            by_model[ref]["quality"].append(pct)
        # Vitesse
        elif m in ("speed_tps", "tokens_per_second"):
            by_model[ref]["speed"].append(pct)
        # Coût (inversé : percentile bas = cher = mauvais)
        elif m in ("cost_per_m_input", "cost_per_m_output"):
            by_model[ref]["cost"].append(100 - pct)  # inversé

    results = []
    for model_ref, scores in by_model.items():
        efficacy = {
            "model_ref": model_ref,
            "use_case": "general",  # use_case unique pour l'instant
            "score_quality": round(statistics.mean(scores["quality"]), 1) if scores["quality"] else 0.0,
            "score_speed": round(statistics.mean(scores["speed"]), 1) if scores["speed"] else 0.0,
            "score_cost": round(statistics.mean(scores["cost"]), 1) if scores["cost"] else 0.5,
            "score_reliability": 0.5,  # défaut, sera affiné par l'usage
        }
        # Score global = moyenne pondérée
        weights = {"score_quality": 0.4, "score_speed": 0.2, "score_cost": 0.2, "score_reliability": 0.2}
        global_score = sum(
            efficacy[k] * weights[k] for k in weights
        ) / sum(weights.values())
        efficacy["samples"] = len(scores["quality"]) + len(scores["speed"]) + len(scores["cost"])
        results.append({"model_ref": model_ref, "global_score": round(global_score, 1), **efficacy})

    return results


def get_remote_connection():
    """Retourne une connexion à la base Turso distante.

    Lit TURSO_URL et TURSO_TOKEN depuis l'environnement.
    """
    import os
    url = os.getenv("TURSO_URL")
    token = os.getenv("TURSO_TOKEN")
    if not url or not token:
        raise RuntimeError(
            "TURSO_URL et TURSO_TOKEN doivent être définis dans .env\n"
            "Copier depuis le serveur admin."
        )
    try:
        import libsql
        return libsql.connect(url, auth_token=token)
    except ImportError:
        raise RuntimeError("libsql non installé. pip install libsql")


def get_local_catalogue():
    """Retourne une connexion à la base catalogue locale."""
    import sqlite3
    from pathlib import Path
    home = Path(os.environ.get("MODELWEAVER_HOME", Path.home() / ".modelweaver"))
    db_path = home / "catalogue.db"
    if not db_path.exists():
        raise RuntimeError(f"Catalogue DB not found at {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_model_id(conn, model_ref: str) -> Optional[int]:
    """Trouve le catalogue_models.id pour un model_ref canonique."""
    row = conn.execute(
        "SELECT id FROM catalogue_models WHERE ref = ?", (model_ref,)
    ).fetchone()
    if row:
        return row[0] if isinstance(row, sqlite3.Row) else row["id"]
    return None


def write_raw(conn, rows: List[Dict[str, Any]]):
    """Écrit les données brutes dans model_benchmarks_raw."""
    for row in rows:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO model_benchmarks_raw
                    (model_ref, benchmark_key, metric_name, raw_value, percentile, source_url)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                row["model_ref"],
                row["benchmark_key"],
                row["metric_name"],
                row["raw_value"],
                row.get("percentile"),
                row.get("source_url", ""),
            ))
        except Exception as e:
            print(f"    [write] {row['model_ref']}/{row['benchmark_key']}: {e}")
    conn.commit()


def write_efficacy(conn, efficacy_rows: List[Dict[str, Any]]):
    """Écrit les scores consolidés dans model_efficacy.

    Note : model_efficacy utilise model_id (FK), pas model_ref directement.
    On résout via catalogue_models.ref.
    """
    import sqlite3
    for row in efficacy_rows:
        model_id = _resolve_model_id(conn, row["model_ref"])
        if not model_id:
            continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO model_efficacy
                    (model_id, use_case, score_quality, score_speed, score_cost,
                     score_reliability, samples)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                model_id,
                row["use_case"],
                row["score_quality"],
                row["score_speed"],
                row["score_cost"],
                row["score_reliability"],
                row.get("samples", 1),
            ))
        except Exception as e:
            print(f"    [efficacy] {row['model_ref']}: {e}")
    conn.commit()


def run(local_mode: bool = False):
    """Point d'entrée : lit raw → percentile → efficacy.

    Args:
        local_mode: si True, écrit sur la base catalogue locale
                    sinon, écrit sur Turso (distant)
    """
    from .sources import fetch_all

    print("Fetching all sources...")
    raw_by_source = fetch_all()

    all_raw = []
    for source_rows in raw_by_source.values():
        all_raw.extend(source_rows)

    print(f"\nTotal raw rows: {len(all_raw)}")
    if not all_raw:
        print("Nothing to consolidate.")
        return

    # Percentiles
    print("Computing percentiles...")
    all_raw = compute_percentiles(all_raw)

    # Consolidation
    print("Consolidating to efficacy scores...")
    efficacy = consolidate_to_efficacy(all_raw)
    print(f"  {len(efficacy)} models scored")

    # Écriture
    print(f"\nWriting to {'local catalogue' if local_mode else 'Turso remote'}...")
    if local_mode:
        conn = get_local_catalogue()
    else:
        conn = get_remote_connection()

    # S'assurer que la table raw existe
    try:
        from pathlib import Path
        schema_sql = Path(__file__).resolve().parent / "schema.sql"
        if schema_sql.exists():
            conn.executescript(schema_sql.read_text())
            conn.commit()
    except Exception:
        pass

    write_raw(conn, all_raw)
    write_efficacy(conn, efficacy)

    if hasattr(conn, "close"):
        conn.close()

    print("\nDone.")
