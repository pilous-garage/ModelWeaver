"""llm_usage/profileur — PROFILEUR DE CONSOMMATION (Idée 18, O3/O11).

Caractérise le comportement de consommation d'un agent — burst | constant |
unknown — à partir de model_call_log (appels réels par agent_id, fenêtre
récente). Le profil MESURÉ surcharge la déclaration du manifest (llm_call_type)
car les utilisateurs ne savent pas ce qu'ils font.

Critère :
  - BURST : N appels concentrés en rafales (pics courts) séparés par du
    silence (≥ burst_gap_s). L'agent consomme par à-coups → le réallocateur
    peut libérer vite sa ressource (le pic est fini).
  - CONSTANT : appels réguliers (intervalles entre appels faibles, pas de long
    silence) → l'agent garde son allocation.
  - UNKNOWN : pas assez d'appels dans la fenêtre pour conclure.

Best-effort : ne lève jamais.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# Fenêtre d'observation (appels récents pris en compte).
WINDOW_S = 10 * 60          # 10 min
# Silence minimal qui caractérise un burst (entre rafales).
BURST_GAP_S = 120           # 2 min sans appel = fin d'une rafale
# Nb minimal d'appels pour conclure.
MIN_CALLS = 5
# Si ≥ cette proportion d'appels sont dans des rafales → burst.
BURST_RATIO = 0.6


def _now() -> int:
    return int(time.time())


def profile_agent(cat, agent_id: str,
                  window_s: int = WINDOW_S,
                  declared: str = "") -> Dict[str, Any]:
    """Profil de consommation d'un agent sur la fenêtre récente.

    `declared` : le llm_call_type du manifest (burst/constant/unknown) — le
    profil mesuré le surcharge si on a assez de données.
    Retourne {behaviour, declared, measured, samples, details}.
    """
    try:
        cutoff = _now() - window_s
        rows = cat.conn.execute(
            "SELECT created_at FROM model_call_log "
            "WHERE agent_id = ? AND created_at >= ? ORDER BY created_at",
            (agent_id, cutoff)).fetchall()
        ts = [r["created_at"] for r in rows]
        n = len(ts)
        if n < MIN_CALLS:
            return {
                "behaviour": "unknown",
                "declared": declared,
                "measured": "unknown",
                "samples": n,
                "reason": f"trop peu d'appels ({n}<{MIN_CALLS}) dans la fenêtre",
            }
        # Intervalles entre appels consécutifs.
        gaps = [ts[i + 1] - ts[i] for i in range(n - 1)]
        max_gap = max(gaps) if gaps else 0
        avg_gap = sum(gaps) / len(gaps) if gaps else 0
        # Compter les "rafales" : groupes d'appels séparés par un silence ≥ gap.
        bursts = 0
        in_burst = False
        burst_calls = 0
        for i in range(n):
            if not in_burst:
                in_burst = True
                bursts += 1
            if i < n - 1 and gaps[i] >= BURST_GAP_S:
                in_burst = False
        # Ratio d'appels dans des rafales ≠ le burst au sens strict — on
        # approxime : nb de rafales ≤ N, distribution concentrée = burst.
        if bursts <= 1:
            # un seul groupe : soit un burst (concentré) soit constant.
            # Concentré = durée totale courte + gros écarts → burst.
            span = ts[-1] - ts[0] if n > 1 else 0
            if n >= 3 and span <= window_s * 0.3 and avg_gap <= BURST_GAP_S * 0.5:
                measured = "burst"       # appels très rapprochés, fenêtre courte
            else:
                measured = "constant"    # étalé sur la fenêtre = régulier
        else:
            # Plusieurs rafales séparées par du silence → burst.
            measured = "burst" if max_gap >= BURST_GAP_S else "constant"
        # Déclaré surchargé seulement si on a assez de données.
        behaviour = measured if n >= MIN_CALLS else declared
        return {
            "behaviour": behaviour,
            "declared": declared,
            "measured": measured,
            "samples": n,
            "bursts": bursts,
            "max_gap_s": max_gap,
            "avg_gap_s": round(avg_gap, 1),
        }
    except Exception:
        return {"behaviour": "unknown", "declared": declared,
                "measured": "unknown", "samples": 0}


def profile_agents(cat, agent_ids: Optional[List[str]] = None) -> Dict[str, str]:
    """Profils de tous les agents (ou d'une liste) : agent_id → behaviour."""
    out: Dict[str, str] = {}
    try:
        if agent_ids is None:
            rows = cat.conn.execute(
                "SELECT DISTINCT agent_id FROM model_call_log "
                "WHERE agent_id IS NOT NULL").fetchall()
            agent_ids = [r["agent_id"] for r in rows]
        for aid in agent_ids:
            p = profile_agent(cat, aid)
            out[aid] = p.get("behaviour", "unknown")
        return out
    except Exception:
        return out