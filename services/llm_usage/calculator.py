"""llm_usage/calculator — CALCULATEUR DE COÛTS (Idée 18, section N).

Objectif : estimer le coût d'une (tâche, niveau) pour un (modèle, adresse)
afin que l'allocateur choisisse le modèle le plus approprié au budget.

Décomposition :
  coût(actions) = tokens(modèle) × temps(adresse) × prix(adresse)
Les tables (catalogue) :
  - task_level_cost    : le TRAVAIL d'une (tâche, niveau) — indépendant du LLM.
  - llm_effort_ratio   : le COMPORTEMENT du modèle (tokens/travail, temps/travail).
  - task_level_stats   : ratios par niveau du cost_ref (équilibre combos peu remplis).
  - llm_task_cost      : CACHE du produit (modèle, type, niveau) — re-synthétisée
                        par le tick (jamais écrite ligne par ligne à l'appel).

MISE À JOUR PASSIVE (important au tick) : chaque écriture compare d'abord la
valeur existante — si l'information n'a pas BOUGÉ, on n'écrit PAS (évite les
toucher de lignes inutiles et la ronde des rowcount). Tous les helpers
prennent une `sqlite3.Connection` (PAS un CatalogueDB). Best-effort.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

LEVELS = ("debutant", "junior", "intermediaire", "senior", "expert")

# Ratios par niveau du cost_ref (niveau de référence = senior, ratio 1.0).
# Heuristiques initiales : un niveau junior consomme ~60% du senior, etc.
# DOIT être surchargé par les stats réelles (séquences) au fil de l'expérience.
LEVEL_STATS_SEED = {
    "debutant":      {"tok_in": 0.25, "tok_out": 0.20, "tok_think": 0.10,
                      "req": 0.8, "temps": 0.8},
    "junior":        {"tok_in": 0.45, "tok_out": 0.40, "tok_think": 0.30,
                      "req": 0.9, "temps": 0.9},
    "intermediaire": {"tok_in": 0.70, "tok_out": 0.70, "tok_think": 0.60,
                      "req": 1.0, "temps": 1.0},
    "senior":        {"tok_in": 1.00, "tok_out": 1.00, "tok_think": 1.00,
                      "req": 1.0, "temps": 1.0},
    "expert":        {"tok_in": 1.35, "tok_out": 1.40, "tok_think": 1.60,
                      "req": 1.1, "temps": 1.2},
}


def _seed_task_level_stats(conn) -> None:
    """Seed idempotent des stats par niveau du cost_ref (sénior). Passif. """
    try:
        types = conn.execute(
            "SELECT id, code FROM scoring_task_types").fetchall()
        if not types:
            return
        for t in types:
            for niveau, ratios in LEVEL_STATS_SEED.items():
                conn.execute("""
                    INSERT OR IGNORE INTO task_level_stats
                        (task_type_id, niveau, ref_niveau,
                         tok_in_ratio, tok_out_ratio, tok_think_ratio,
                         req_ratio, temps_ratio, samples)
                    VALUES (?, ?, 'senior', ?, ?, ?, ?, ?, 0)
                """, (t["id"], niveau, ratios["tok_in"], ratios["tok_out"],
                      ratios["tok_think"], ratios["req"], ratios["temps"]))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def _seed_task_level_cost(conn) -> int:
    """Seed du TRAVAIL par (task_type, niveau) depuis les stats du cost_ref.

    Le niveau de référence (senior) vaut 1.0 unité de travail pour le type ;
    chaque niveau = travail du senior × ratio du niveau. La table
    task_level_cost = le poids INTRINSÈQUE (indépendant du LLM), affiné par
    les séquences (tâches complètes). Passif : n'écrit que si valeur changée.
    """
    try:
        types = conn.execute(
            "SELECT id, code FROM scoring_task_types").fetchall()
        if not types:
            return 0
        n = 0
        for t in types:
            stats = {r["niveau"]: r for r in conn.execute(
                "SELECT niveau, tok_in_ratio, tok_out_ratio, tok_think_ratio, "
                "req_ratio, temps_ratio FROM task_level_stats WHERE task_type_id = ?",
                (t["id"],)).fetchall()}
            for niveau in LEVELS:
                s = stats.get(niveau)
                ratio = 1.0
                if s:
                    ratio = (s["tok_in_ratio"] or 1.0) * (s["req_ratio"] or 1.0)
                ratio = round(ratio, 4)
                existing = conn.execute(
                    "SELECT travail FROM task_level_cost "
                    "WHERE task_type_id = ? AND niveau = ?",
                    (t["id"], niveau)).fetchone()
                if existing and abs(float(existing["travail"] or 0) - ratio) < 1e-9:
                    continue  # rien n'a bougé → on n'écrit pas (passif)
                conn.execute("""
                    INSERT INTO task_level_cost
                        (task_type_id, niveau, travail, samples)
                    VALUES (?, ?, ?, 0)
                    ON CONFLICT(task_type_id, niveau) DO UPDATE SET
                        travail = excluded.travail
                """, (t["id"], niveau, ratio))
                n += 1
        conn.commit()
        return n
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def _seed_effort_ratio(conn) -> int:
    """Seed de l'EFFORT de chaque modèle (comportement, tokens/travail).

    Init neutre : 1.0 partout (le modèle moyen). À affiner par les séquences
    réelles (un modèle verbeux aura tok_out_par_travail > 1). Passif.
    """
    try:
        models = conn.execute("SELECT DISTINCT model_id FROM llm_domaine_score").fetchall()
        n = 0
        for m in models:
            cur = conn.execute("""
                INSERT INTO llm_effort_ratio
                    (model_id, tok_in_par_travail, tok_out_par_travail,
                     tok_think_par_travail, req_par_travail, temps_par_travail)
                VALUES (?, 1.0, 1.0, 1.0, 1.0, 1.0)
                ON CONFLICT(model_id) DO NOTHING
            """, (m["model_id"],))
            n += cur.rowcount if hasattr(cur, "rowcount") else 0
        conn.commit()
        return n
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def _thinking_power_for(conn, model_id: int, niveau: str) -> float:
    """thinking_power(modèle, niveau) = Σ_domaines score(domaine,niveau)²
    + Σ_types score(type,niveau)²  (somme GLOBALE, au niveau requis).

    Les pondérations w sont toutes = 1 pour l'instant (réglables plus tard).
    """
    try:
        d = conn.execute(
            "SELECT debutant, junior, intermediaire, senior, expert "
            "FROM llm_domaine_score WHERE model_id = ?", (model_id,)).fetchall()
        t = conn.execute(
            "SELECT debutant, junior, intermediaire, senior, expert "
            "FROM llm_task_type_score WHERE model_id = ?", (model_id,)).fetchall()
        total = 0.0
        for row in d + t:
            total += float(row[niveau] or 1.0) ** 2
        return total
    except Exception:
        return float(len(LEVELS))


def _synthesize_task_cost(conn) -> int:
    """Re-synthétise llm_task_cost (modèle, type, niveau) — le PRODUIT.

    Pour chaque modèle ayant un score : travail(type, niveau) × effort(modèle).
    Passif : on ne met à jour une ligne que si au moins une valeur a bougé.
    Retourne le nombre de lignes réellement écrites.
    """
    try:
        models = conn.execute("SELECT DISTINCT model_id FROM llm_domaine_score").fetchall()
        types = conn.execute("SELECT id FROM scoring_task_types").fetchall()
        work = conn.execute(
            "SELECT task_type_id, niveau, travail FROM task_level_cost").fetchall()
        work_map = {(r["task_type_id"], r["niveau"]): r["travail"] or 1.0
                    for r in work}
        effort = conn.execute(
            "SELECT model_id, tok_in_par_travail, tok_out_par_travail, "
            "tok_think_par_travail, req_par_travail, temps_par_travail "
            "FROM llm_effort_ratio").fetchall()
        effort_map = {r["model_id"]: r for r in effort}
        n = 0
        for m in models:
            mid = m["model_id"]
            ef = effort_map.get(mid)
            ef_in = ef["tok_in_par_travail"] or 1.0 if ef else 1.0
            ef_out = ef["tok_out_par_travail"] or 1.0 if ef else 1.0
            ef_th = ef["tok_think_par_travail"] or 1.0 if ef else 1.0
            ef_req = ef["req_par_travail"] or 1.0 if ef else 1.0
            ef_tm = ef["temps_par_travail"] or 1.0 if ef else 1.0
            for t in types:
                for niveau in LEVELS:
                    w = work_map.get((t["id"], niveau), 1.0)
                    nv = (round(w * ef_in, 4), round(w * ef_out, 4),
                          round(w * ef_th, 4), round(w * ef_req, 4),
                          round(w * ef_tm, 4))
                    existing = conn.execute(
                        "SELECT tok_in, tok_out, tok_think, req, temps "
                        "FROM llm_task_cost "
                        "WHERE model_id = ? AND task_type_id = ? AND niveau = ?",
                        (mid, t["id"], niveau)).fetchone()
                    if existing is not None:
                        # Passif : ne réécrit que si une valeur a changé.
                        e = (round(float(existing["tok_in"] or 0), 4),
                             round(float(existing["tok_out"] or 0), 4),
                             round(float(existing["tok_think"] or 0), 4),
                             round(float(existing["req"] or 0), 4),
                             round(float(existing["temps"] or 0), 4))
                        if e == nv:
                            continue
                        conn.execute("""
                            UPDATE llm_task_cost SET
                                tok_in = ?, tok_out = ?, tok_think = ?, req = ?,
                                temps = ?, updated_at = strftime('%s','now')
                            WHERE model_id = ? AND task_type_id = ? AND niveau = ?
                        """, (*nv, mid, t["id"], niveau))
                    else:
                        conn.execute("""
                            INSERT INTO llm_task_cost
                                (model_id, task_type_id, niveau, tok_in, tok_out,
                                 tok_think, req, temps, thinking_power, money,
                                 confiance, samples, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0,
                                    strftime('%s','now'))
                        """, (mid, t["id"], niveau, *nv))
                    n += 1
        conn.commit()
        return n
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return 0


def recompute(cat=None, conn=None) -> Dict[str, Any]:
    """Point d'entrée du tick calculateur : seed des stats + synthèse coût.

    Appelé périodiquement (tick). Re-synthétise llm_task_cost à partir du
    travail des tâches, de l'effort des modèles et des scores (thinking_power).
    `conn` = une sqlite3.Connection (ou `cat` = un CatalogueDB). Retourne
    {seeded, work, effort, synthesized, ok}.
    """
    from modules.sql.catalogue_repo import CatalogueDB
    from services.domain_access import guard
    guard("scoring", "calculateur", "task_level_cost")
    cat = cat or CatalogueDB()
    c = conn or cat.conn
    _seed_task_level_stats(c)
    work_n = _seed_task_level_cost(c)
    effort_n = _seed_effort_ratio(c)
    n = _synthesize_task_cost(c)
    return {"seeded": True, "work": work_n, "effort": effort_n,
            "synthesized": n, "ok": True}


if __name__ == "__main__":
    r = recompute()
    print("recompute:", r)