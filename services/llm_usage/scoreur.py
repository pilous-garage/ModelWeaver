"""llm_usage/scoreur — SCOREUR D'EXPÉRIENCE (Idée 18, section D/I/N).

Le scoreur met à jour les scores d'expérience à partir des ÉVÉNEMENTS
(pipelines de tâches terminées, benchmarks, signaux supervisor) puis DÉRIVE
les tables thinking_power — c'est le scoreur qui s'en occupe, jamais l'appel.

Mécanique (section D : fraîcheur + stabilité) :
  - un événement (root_id, llm_id, rôle, domaine, task_type, niveau, gain)
    ENRICHIT le score hexistant au lieu de le remplacer ;
  - fraîcheur : les événements récents pèsent + que les anciens (demi-vie) ;
  - stabilité : un score ne change pas brusquement (plancher de variation).
  - gain : +1 = succès (bonus), -1 = échec (pénalité), 0 = neutre/samples only.

Formule de mise à jour (approche batch, non instantanée) :
  nouveau = ancien + pas × sign(gain − ancien)  avec pas = w×alpha
  où w = poids de fraîcheur (décroît avec l'âge), alpha = taux d'apprentissage.

Dérivation thinking_power (section N6) :
  thinking_power(modèle, niveau) = Σ_domaines w×score(domaine,niveau)²
                                  + Σ_types w×score(type,niveau)²
  (somme GLOBALE, au niveau requis, w = 1 pour l'instant).
  - thinking_power_model : par (model_id, niveau) — l'indice cognitif du modèle.
  - thinking_power_adress : par (adresse_runtime_id, niveau) — le modèle servi
    par une adresse (même modèle = même puissance cognitive).

Best-effort : ne lève jamais.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

LEVELS = ("debutant", "junior", "intermediaire", "senior", "expert")

# Pas d'apprentissage (alpha) et plancher de variation (stabilité).
ALPHA = 0.1
MIN_VARIATION = 0.02
MIN_SCORE = 0.05
MAX_SCORE = 2.0

# Demi-vie de fraîcheur : un événement de plus de N secondes pèse moitié moins.
HALF_LIFE_S = 7 * 86400  # 7 jours


def _now() -> int:
    return int(time.time())


def _fresh_weight(event_age_s: float) -> float:
    """Poids de fraîcheur d'un événement : w = 0.5^(age/demi-vie)."""
    if HALF_LIFE_S <= 0:
        return 1.0
    return 0.5 ** (max(0.0, event_age_s) / HALF_LIFE_S)


def _bump_score(current: float, gain: float) -> float:
    """Met à jour un score AVEC stabilité : nouveau = ancien + pas×DeltaTarget.

    Le pas = ALPHA + des bornes MIN_VARIATION/MIN_SCORE/MAX_SCORE. Un score ne
    bouge que si l'événement va DANS le sens opposé à la tendance (sinon on le
    confirme sans le faire bouger beaucoup)."""
    cur = float(current or 1.0)
    # direction : +1 = doit monter, -1 = doit baisser
    direction = 1.0 if gain > 0 else -1.0
    # delta vers la cible (1.0 en cas de succès, 0.05 en cas d'échec → on ne
    # descend jamais en dessous du plancher MIN_SCORE).
    target = 1.0 if gain > 0 else MIN_SCORE
    delta = target - cur
    step = direction * ALPHA * abs(delta)
    if abs(step) < MIN_VARIATION:
        step = direction * MIN_VARIATION
    new = cur + step
    return round(min(MAX_SCORE, max(MIN_SCORE, new)), 4)


def update_experience_scores(cat, events: Iterable[Dict[str, Any]]) -> int:
    """Met à jour les scores d'expérience (domaine + task_type) pour une
    liste d'événements de fin de pipeline.

    Chaque événement :
      model_id     : le modèle évalué
      domaine      : code domaine (ou None) — score llm_domaine_score
      task_type    : code type de tâche (ou None) — score llm_task_type_score
      niveau       : 'debutant'..'expert'
      gain         : +1 (succès) / -1 (échec) / 0 (samples only)
      age          : âge de l'événement en secondes (fraîcheur)
    Retourne le nombre de scores mis à jour.
    """
    try:
        n = 0
        for ev in events:
            mid = ev.get("model_id")
            if not mid:
                continue
            niveau = ev.get("niveau")
            if niveau not in LEVELS:
                continue
            gain = float(ev.get("gain", 0))
            age = float(ev.get("age", 0))
            w = _fresh_weight(age)
            gain_w = gain * w
            # ── Domaine ──
            dom = ev.get("domaine")
            if dom:
                row = cat.conn.execute(
                    "SELECT d.id, s.%s FROM scoring_domaines d "
                    "JOIN llm_domaine_score s ON s.domaine_id = d.id "
                    "WHERE s.model_id = ? AND d.code = ?" % niveau,
                    (mid, dom)).fetchone()
                if row:
                    newv = _bump_score(row[niveau], gain_w)
                    cat.conn.execute(
                        f"UPDATE llm_domaine_score SET {niveau} = ?, "
                        f"samples = samples + 1, updated_at = ? "
                        f"WHERE model_id = ? AND domaine_id = ?",
                        (newv, _now(), mid, row["id"]))
                    n += 1
            # ── Type de tâche ──
            tt = ev.get("task_type")
            if tt:
                row = cat.conn.execute(
                    "SELECT t.id, s.%s FROM scoring_task_types t "
                    "JOIN llm_task_type_score s ON s.task_type_id = t.id "
                    "WHERE s.model_id = ? AND t.code = ?" % niveau,
                    (mid, tt)).fetchone()
                if row:
                    newv = _bump_score(row[niveau], gain_w)
                    cat.conn.execute(
                        f"UPDATE llm_task_type_score SET {niveau} = ?, "
                        f"samples = samples + 1, updated_at = ? "
                        f"WHERE model_id = ? AND task_type_id = ?",
                        (newv, _now(), mid, row["id"]))
                    n += 1
        cat.conn.commit()
        # Après chaque batch d'événements, on re-dérive le thinking_power.
        derive_thinking_power(cat)
        return n
    except Exception:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return 0


def derive_thinking_power(cat) -> Dict[str, Any]:
    """Re-dérive thinking_power_model + thinking_power_adress depuis les scores.

    thinking_power(modèle, niveau) = Σ_domaines score(domaine,niveau)²
                                    + Σ_types score(type,niveau)²
    (somme GLOBALE, au niveau requis ; w = 1 pour l'instant).
    Adresse = modèle servi par une adresse → même valeur que le modèle.
    Passif : ne réécrit une ligne que si la valeur a changé.
    """
    try:
        # Indice par (model_id, niveau)
        model_power: Dict[Tuple[int, str], float] = {}
        d = cat.conn.execute(
            "SELECT model_id, domaine_id, debutant, junior, intermediaire, "
            "senior, expert FROM llm_domaine_score").fetchall()
        t = cat.conn.execute(
            "SELECT model_id, task_type_id, debutant, junior, intermediaire, "
            "senior, expert FROM llm_task_type_score").fetchall()
        # accumuler les sommes des carrés par (model, niveau)
        for r in d:
            for lvl in LEVELS:
                model_power.setdefault((r["model_id"], lvl), 0.0)
                model_power[(r["model_id"], lvl)] += float(r[lvl] or 1.0) ** 2
        for r in t:
            for lvl in LEVELS:
                model_power.setdefault((r["model_id"], lvl), 0.0)
                model_power[(r["model_id"], lvl)] += float(r[lvl] or 1.0) ** 2

        n_model = n_adress = 0
        for key, val in model_power.items():
            mid, lvl = key
            row = cat.conn.execute(
                "SELECT thinking_power FROM thinking_power_model "
                "WHERE model_id = ? AND niveau = ?", (mid, lvl)).fetchone()
            val = round(val, 4)
            if row is not None:
                if abs(float(row["thinking_power"] or 0) - val) >= 1e-9:
                    cat.conn.execute(
                        "UPDATE thinking_power_model SET thinking_power = ?, "
                        "updated_at = ? WHERE model_id = ? AND niveau = ?",
                        (val, _now(), mid, lvl))
                    n_model += 1
            else:
                cat.conn.execute(
                    "INSERT INTO thinking_power_model (model_id, niveau, thinking_power) "
                    "VALUES (?, ?, ?)", (mid, lvl, val))
                n_model += 1

        # Adresses : le modèle servi par l'adresse (même puissance cognitive).
        adr = cat.conn.execute(
            "SELECT adresse_runtime_id, model_id FROM adresse_runtime").fetchall()
        for a in adr:
            mid = a["model_id"]
            if mid is None:
                continue
            for lvl in LEVELS:
                val = round(model_power.get((mid, lvl), 0.0), 4)
                row = cat.conn.execute(
                    "SELECT thinking_power FROM thinking_power_adress "
                    "WHERE adresse_runtime_id = ? AND niveau = ?",
                    (a["adresse_runtime_id"], lvl)).fetchone()
                if row is not None:
                    if abs(float(row["thinking_power"] or 0) - val) >= 1e-9:
                        cat.conn.execute(
                            "UPDATE thinking_power_adress SET thinking_power = ?, "
                            "updated_at = ? WHERE adresse_runtime_id = ? AND niveau = ?",
                            (val, _now(), a["adresse_runtime_id"], lvl))
                        n_adress += 1
                else:
                    cat.conn.execute(
                        "INSERT INTO thinking_power_adress "
                        "(adresse_runtime_id, niveau, thinking_power) VALUES (?, ?, ?)",
                        (a["adresse_runtime_id"], lvl, val))
                    n_adress += 1
        cat.conn.commit()
        return {"model": n_model, "adresse": n_adress, "ok": True}
    except Exception:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return {"model": 0, "adresse": 0, "ok": False}


def thinking_power(cat, model_id: Optional[int] = None,
                   adresse_runtime_id: Optional[int] = None,
                   niveau: str = "senior") -> float:
    """Lecture du thinking_power (côté modèle ou côté adresse)."""
    try:
        if adresse_runtime_id is not None:
            row = cat.conn.execute(
                "SELECT thinking_power FROM thinking_power_adress "
                "WHERE adresse_runtime_id = ? AND niveau = ?",
                (adresse_runtime_id, niveau)).fetchone()
        elif model_id is not None:
            row = cat.conn.execute(
                "SELECT thinking_power FROM thinking_power_model "
                "WHERE model_id = ? AND niveau = ?", (model_id, niveau)).fetchone()
        else:
            return 0.0
        return float(row["thinking_power"] or 0) if row else 0.0
    except Exception:
        return 0.0