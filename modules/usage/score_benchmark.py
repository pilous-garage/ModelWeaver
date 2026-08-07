"""score_benchmark — étirement des scores benchmark (model_efficacy) par modèle.

Principe (validé utilisateur) :
  - source : model_efficacy (catalogue.db), scores 0-100 par spécialité
    (global, chat, coding, reasoning, knowledge, agentic).
  - étirement par min/max de colonne :
        score_etire = (score - min_col) / (max_col - min_col) * 0.8 + 0.1
    avec min_col/max_col = bornes réelles de la colonne (parmi les scores > 0).
  - Les bornes UTILISÉES sont persistées dans score_benchmark_meta (une ligne
    par colonne). Pour un NOUVEAU modèle on applique le même étirement
    (score - min)/(max - min) sans recalculer la colonne.
  - Si un score sort des bornes stockées (nouveau min/max) → recalcule de
    TOUTE la colonne (mise à jour des bornes + tous les étirés).
  - Fallback par spécialité : modèle sans score spécialité → score_etire du
    GLOBAL (src_* = 'global'). Modèle sans AUCUN score → baseline 0.1.
  - score_etire (colonne principale) = score_etire_global (défaut) ; il sert
    de référence si l'allocation n'utilise pas de spécialité.

Appelé par usage_batcher.run_once (1x/cycle) — idempotent, incrémental : ne
retouche que les lignes dont le score source a changé ou les colonnes à
recalculer.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Colonnes score source (model_efficacy) → colonne score_etire cible.
SPECS = (
    ("global",    "score_etire"),
    ("chat",      "score_etire_chat"),
    ("coding",    "score_etire_coding"),
    ("reasoning", "score_etire_reasoning"),
    ("knowledge", "score_etire_knowledge"),
    ("agentic",   "score_etire_agentic"),
)
# model_efficacy utilise global_score (pas score_global) pour la spécialité
# 'global' ; les autres sont score_<spec>.
_SRC_FIELD = {spec: ("global_score" if spec == "global" else f"score_{spec}")
              for spec, _ in SPECS}

BASELINE = 0.1
RANGE = 0.8  # max 0.9 - min 0.1


def _stretch(score: float, min_col: float, max_col: float) -> float:
    """Étirement d'un score (0-100) dans [0.1, 0.9] selon les bornes de colonne."""
    if max_col <= min_col:
        return BASELINE
    ratio = (score - min_col) / (max_col - min_col)
    ratio = max(0.0, min(1.0, ratio))
    return BASELINE + ratio * RANGE


def _read_source(cat) -> List[dict]:
    """Les scores bruts par modèle depuis model_efficacy (is_synthetic OK)."""
    fields = ", ".join(_SRC_FIELD[s] for s, _ in SPECS)
    return [dict(r) for r in cat.conn.execute(f"""
        SELECT model_ref, is_synthetic, {fields}
        FROM model_efficacy
        WHERE model_ref IS NOT NULL AND model_ref != ''
    """).fetchall()]


def _read_meta(rt) -> Dict[str, dict]:
    rows = rt.conn.execute(
        "SELECT column_name, min_value, max_value FROM score_benchmark_meta"
    ).fetchall()
    return {r["column_name"]: {"min": r["min_value"], "max": r["max_value"]}
            for r in rows}


def _write_meta(rt, meta: Dict[str, dict]):
    for spec, bounds in meta.items():
        rt.conn.execute("""
            INSERT OR REPLACE INTO score_benchmark_meta
                (column_name, min_value, max_value, updated_at)
            VALUES (?, ?, ?, strftime('%s','now'))
        """, (spec, bounds["min"], bounds["max"]))
    rt.conn.commit()


def _column_bounds(rows: List[dict], spec: str) -> Dict[str, float]:
    vals = [r[_SRC_FIELD[spec]] for r in rows
            if (r[_SRC_FIELD[spec]] or 0) > 0]
    if not vals:
        return {"min": 0.0, "max": 1.0}
    return {"min": min(vals), "max": max(vals)}


def compute_etire(cat, rt) -> Dict[str, Any]:
    """Calcule/met à jour score_benchmark_etire depuis model_efficacy.

    Retourne {inserted, updated, recalc_cols, total}.
    """
    rows = _read_source(cat)
    meta = _read_meta(rt)
    if not rows:
        return {"inserted": 0, "updated": 0, "recalc_cols": [], "total": 0}

    # 1. Bornes réelles par colonne → détecter les bornes qui changent.
    real_bounds = {spec: _column_bounds(rows, spec) for spec, _ in SPECS}
    recalc_cols = [
        spec for spec, _ in SPECS
        if spec not in meta
        or abs(meta[spec]["min"] - real_bounds[spec]["min"]) > 1e-6
        or abs(meta[spec]["max"] - real_bounds[spec]["max"]) > 1e-6
    ]
    # Mettre à jour les bornes (toute colonne à recalculer OU inconnue).
    for spec, _ in SPECS:
        meta[spec] = real_bounds[spec]
    if recalc_cols:
        _write_meta(rt, meta)

    # 2. Lignes existantes (model_ref → score source) pour ne réécrire que les
    #    modèles dont un score a changé (ou nouveaux).
    existing = {}
    for r in rt.conn.execute(
            "SELECT model_ref, score_global, score_etire_chat, score_etire_coding, "
            "score_etire_reasoning, score_etire_knowledge, score_etire_agentic "
            "FROM score_benchmark_etire").fetchall():
        existing[r["model_ref"]] = dict(r)

    inserted = updated = 0
    for r in rows:
        model_ref = r["model_ref"]
        # Valeurs étirées pour chaque spécialité (fallback global).
        global_etire = _stretch(r[_SRC_FIELD["global"]],
                                meta["global"]["min"], meta["global"]["max"]) \
            if (r[_SRC_FIELD["global"]] or 0) > 0 else BASELINE
        etires = {}
        srcs = {}
        for spec, col in SPECS:
            score = r[_SRC_FIELD[spec]] or 0
            if score > 0:
                etires[col] = _stretch(score, meta[spec]["min"], meta[spec]["max"])
                srcs[spec] = "spec"
            elif spec == "global":
                etires[col] = BASELINE
                srcs[spec] = "none"
            else:
                etires[col] = global_etire
                srcs[spec] = "global"
        prev = existing.get(model_ref)
        if prev is not None:
            # Ligne inchangée (score global + tous les étirés identiques) → skip.
            unchanged = (abs((prev.get("score_global") or 0) - (r[_SRC_FIELD["global"]] or 0)) < 1e-6
                         and all(abs(prev.get(col) - v) < 1e-6
                                 for col, v in etires.items()
                                 if col != "score_etire"))
            if unchanged and model_ref in existing:
                continue
            updated += 1
        else:
            inserted += 1
        rt.conn.execute("""
            INSERT OR REPLACE INTO score_benchmark_etire
                (model_ref, score_global, score_etire,
                 score_etire_chat, score_etire_coding, score_etire_reasoning,
                 score_etire_knowledge, score_etire_agentic,
                 src_chat, src_coding, src_reasoning, src_knowledge, src_agentic,
                 is_synthetic, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%s','now'))
        """, (model_ref, r[_SRC_FIELD["global"]] or 0, etires["score_etire"],
              etires["score_etire_chat"], etires["score_etire_coding"],
              etires["score_etire_reasoning"], etires["score_etire_knowledge"],
              etires["score_etire_agentic"],
              srcs["chat"], srcs["coding"], srcs["reasoning"],
              srcs["knowledge"], srcs["agentic"],
              r["is_synthetic"] or 0))
    try:
        rt.conn.commit()
    except Exception:
        try:
            rt.conn.rollback()
        except Exception:
            pass
    return {"inserted": inserted, "updated": updated,
            "recalc_cols": recalc_cols, "total": len(rows)}


def main() -> None:
    from modules.sql.db import CatalogueDB, RuntimeDB
    cat = CatalogueDB()
    rt = RuntimeDB()
    print("compute_etire →", compute_etire(cat, rt))
    print(f"  lignes score_benchmark_etire: "
          f"{rt.conn.execute('SELECT COUNT(*) c FROM score_benchmark_etire').fetchone()['c']}")


if __name__ == "__main__":
    main()
