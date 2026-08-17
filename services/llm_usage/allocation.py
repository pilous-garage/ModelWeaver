"""llm_usage/allocation — ALLOCATION AGENT → BUDGET (Idée 18).

Quand on donne un LLM à un agent, on lui donne un budget alloué sur le
budget_final de son adresse. Deux natures, différenciées par reset_suivi :

  - ALLOCATION QUOTA : fraction du quota_final (ex. 0.20 = 20 %). L'agent
    respecte sa limite ET bénéficie des resets de quota : à l'échéance
    (reset_suivi = timestamp du prochain reset du parent), spent se remet
    à 0 et l'allocation se recalcule (fraction × nouveau quota_effectif).
  - ALLOCATION BUDGET : enveloppe FIXE (montant) SANS reset (reset_suivi =
    -1). Elle ne suit pas les resets du parent ; elle s'épuise une fois.

La SOUPLESSE vit ICI (strict/souple/informatif + taux), pas sur budget_final :
le budget_final alloue la part engagée, l'allocation règle le comportement
de dépassement de l'agent sur SA part. Best-effort, ne lève jamais.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


def _now() -> int:
    return int(time.time())


def _resolve_allocation(cat, agent_id: str,
                        adresse_runtime_id: int,
                        nature: str) -> Optional[Dict[str, Any]]:
    """L'allocation ACTIVE d'un agent sur une adresse (nature quota ou budget)."""
    try:
        row = cat.conn.execute(
            "SELECT * FROM agent_budget_allocation "
            "WHERE agent_id = ? AND adresse_runtime_id = ? AND nature = ? "
            "AND status = 'active' ORDER BY allocation_id DESC LIMIT 1",
            (agent_id, adresse_runtime_id, nature)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _recompute_at_reset(cat, alloc: Dict[str, Any]) -> Dict[str, Any]:
    """Ré-évalue une allocation QUOTA quand son reset est échu.

    Ne concernE QUE les allocations de nature 'quota' avec reset_suivi != -1 :
    le parent budget_final a une fenêtre (ex. jour) — à l'échéance on remet
    spent=0 et on recalcule montant = fraction × quota_effectif(actuel).
    Retourne l'allocation à jour (dict), ou l'originale si rien à faire.
    """
    try:
        if alloc["nature"] != "quota":
            return alloc
        nxt = alloc.get("reset_suivi")
        if nxt is None or nxt == -1:
            return alloc
        if _now() < nxt:
            return alloc
        # Reset échu : recalcul montant = fraction × quota_final actuel.
        bf = cat.conn.execute(
            "SELECT quota_effectif, next_reset, interval_reset FROM budget_final "
            "WHERE budget_final_id = ?", (alloc["budget_final_id"],)).fetchone()
        if not bf:
            return alloc
        quota = float(bf["quota_effectif"] or 0)
        frac = float(alloc.get("fraction") or 0)
        new_montant = quota * frac
        cat.conn.execute(
            "UPDATE agent_budget_allocation SET spent = 0, "
            "montant = ?, reset_suivi = ?, updated_at = ? "
            "WHERE allocation_id = ?",
            (new_montant, bf["next_reset"] or -1, _now(), alloc["allocation_id"]))
        cat.conn.commit()
        alloc["spent"] = 0
        alloc["montant"] = new_montant
        alloc["reset_suivi"] = bf["next_reset"] or -1
        return alloc
    except Exception:
        return alloc


def allocate(cat, agent_id: str, adresse_runtime_id: int,
             budget_final_id: int, nature: str = "quota",
             fraction: float = 0.0, montant: float = 0.0,
             souplesse: str = "strict", souplesse_taux: float = 0.0) -> Optional[int]:
    """Crée une allocation agent→budget sur le budget_final d'une adresse.

    - nature='quota'  : fraction (ex. 0.20) du quota_effectif du budget_final ;
      montant = fraction × quota ; reset_suivi = next_reset du parent.
    - nature='budget' : montant fixe ; reset_suivi = -1 (ne suit pas les resets).
    L'ancienne allocation active du même (agent, adresse, nature) est close.
    Retourne l'allocation_id créé, sinon None.
    """
    from services.domain_access import guard
    guard("allocation", "allocateur", "agent_budget_allocation")
    try:
        # Fermer l'ancienne allocation active du même agent sur cette adresse.
        try:
            cat.conn.execute(
                "UPDATE agent_budget_allocation SET status = 'closed', "
                "updated_at = ? WHERE agent_id = ? AND adresse_runtime_id = ? "
                "AND nature = ? AND status = 'active'",
                (_now(), agent_id, adresse_runtime_id, nature))
        except Exception:
            pass
        bf = cat.conn.execute(
            "SELECT quota_effectif, next_reset, interval_reset FROM budget_final "
            "WHERE budget_final_id = ?", (budget_final_id,)).fetchone()
        if not bf:
            return None
        quota = float(bf["quota_effectif"] or 0)
        if nature == "quota":
            montant = quota * float(fraction or 0)
            reset = bf["next_reset"] if bf["next_reset"] else -1
        else:
            montant = float(montant or 0)
            reset = -1
        cur = cat.conn.execute("""
            INSERT INTO agent_budget_allocation
                (agent_id, adresse_runtime_id, budget_final_id, nature,
                 fraction, montant, reset_suivi, interval_reset,
                 souplesse, souplesse_taux, spent, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'active')
        """, (agent_id, adresse_runtime_id, budget_final_id, nature,
              float(fraction or 0), montant, reset, bf["interval_reset"] or "",
              souplesse, souplesse_taux))
        cat.conn.commit()
        # Le budget_final engage la part allouée (réservée, pas consommée).
        cat.conn.execute(
            "UPDATE budget_final SET alloue = alloue + ? "
            "WHERE budget_final_id = ?", (montant, budget_final_id))
        cat.conn.commit()
        return cur.lastrowid if hasattr(cur, "lastrowid") else None
    except Exception:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return None


def consume_allocation(cat, agent_id: str, adresse_runtime_id: int,
                       value: float, nature: str = "quota") -> bool:
    """Consomme `value` sur l'allocation active de l'agent pour cette adresse.

    Applique la SOUPLESSE :
      - strict     : refuse si spent + value > montant (False).
      - souple     : autorise jusqu'à montant × (1 + souplesse_taux), sinon False.
      - informatif : toujours autorise, mais spent continue d'être compté.
    Retourne True si l'usage est autorisé (et compté), False si dépassé.
    """
    try:
        alloc = _resolve_allocation(cat, agent_id, adresse_runtime_id, nature)
        if not alloc:
            # Pas d'allocation = pas de limite spécifique sur l'agent : libre.
            return True
        alloc = _recompute_at_reset(cat, alloc)
        spent = float(alloc.get("spent") or 0)
        mt = float(alloc.get("montant") or 0)
        _souplesse = alloc.get("souplesse") or "strict"
        _taux = float(alloc.get("souplesse_taux") or 0)
        limit = mt
        if _souplesse == "souple":
            limit = mt * (1.0 + _taux)
        elif _souplesse == "informatif":
            limit = float("inf")
        if spent + value > limit:
            return False
        cat.conn.execute(
            "UPDATE agent_budget_allocation SET spent = spent + ?, "
            "updated_at = ? WHERE allocation_id = ?",
            (value, _now(), alloc["allocation_id"]))
        cat.conn.commit()
        return True
    except Exception:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return True  # best-effort : ne bloque pas l'appel sur une erreur


def remaining(cat, agent_id: str, adresse_runtime_id: int,
              nature: str = "quota") -> float:
    """Budget restant de l'allocation active de l'agent (0 si aucune/épuisée)."""
    try:
        alloc = _resolve_allocation(cat, agent_id, adresse_runtime_id, nature)
        if not alloc:
            return 0.0
        alloc = _recompute_at_reset(cat, alloc)
        return float(alloc.get("montant") or 0) - float(alloc.get("spent") or 0)
    except Exception:
        return 0.0