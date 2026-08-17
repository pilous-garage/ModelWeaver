"""llm_usage/reallocateur — RÉALLOCATEUR DE BUDGET (Idée 18, O3/O11).

Détecte la SOUS-UTILISATION des allocations agent→budget et libère/réaffecte
la ressource. Deux signaux :
  1. PROFIL de consommation : un agent BURST (rafales puis silence) a fini son
     pic → sa ressource peut être libérée vite. Un agent CONSTANT la conserve.
  2. UTILISATION RÉELLE : une allocation dont spent/montant est resté très bas
     pendant N minutes est sous-utilisée → on la libère (ou on réduit).

Après libération : la part libérée retourne au budget_final (spent/libre) et
peut être réaffectée à une autre tâche (priorité à l'échéance/priorité).

Best-effort : ne lève jamais.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# Seuil d'utilisation : si spent/montant < ce ratio → sous-utilisé.
UNDERUSE_RATIO = 0.10
# Minutes sans activité avant de considérer une allocation comme dormante.
IDLE_MINUTES = 10

# Seuils pour le profileur (réutilisés du profileur).
BURST_GAP_S = 120


def _now() -> int:
    return int(time.time())


def _active_allocations(cat) -> List[Dict[str, Any]]:
    try:
        rows = cat.conn.execute(
            "SELECT * FROM agent_budget_allocation WHERE status = 'active'"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _agent_activity_since(cat, agent_id: str) -> int:
    """Timestamp du dernier appel LLM de l'agent (0 si jamais)."""
    try:
        row = cat.conn.execute(
            "SELECT MAX(created_at) m FROM model_call_log "
            "WHERE agent_id = ?", (agent_id,)).fetchone()
        return int(row["m"]) if row and row["m"] else 0
    except Exception:
        return 0


def _underuse(alloc: Dict[str, Any]) -> bool:
    """L'allocation est-elle sous-utilisée (spent/montant < seuil) ?"""
    try:
        mt = float(alloc.get("montant") or 0)
        spent = float(alloc.get("spent") or 0)
        if mt <= 0:
            return False
        return (spent / mt) < UNDERUSE_RATIO
    except Exception:
        return False


def evaluate(cat, dry_run: bool = True) -> Dict[str, Any]:
    """Évalue les allocations actives : profil + sous-utilisation.

    Retourne {ok, total, to_release, released, details} — si dry_run, on
    évalue sans libérer (renvoie la liste des allocations candidates).
    """
    try:
        from services.llm_usage.profileur import profile_agent
        allocs = _active_allocations(cat)
        total = len(allocs)
        to_release: List[Dict[str, Any]] = []
        released: List[Dict[str, Any]] = []
        for alloc in allocs:
            agent_id = alloc["agent_id"]
            reason = ""
            should = False
            # 1. Profil burst : rafales terminées → libérer.
            try:
                prof = profile_agent(cat, agent_id, declared="")
                if prof.get("measured") == "burst":
                    should = True
                    reason = "profil burst (rafale terminée)"
            except Exception:
                pass
            # 2. Sous-utilisation réelle (spent/montant très bas).
            if not should and _underuse(alloc):
                last = _agent_activity_since(cat, agent_id)
                idle = (_now() - last) > IDLE_MINUTES * 60 if last else True
                if idle:
                    should = True
                    reason = f"sous-utilisé (spent/montant < {UNDERUSE_RATIO}) + inactif"
            if should:
                entry = {"allocation_id": alloc["allocation_id"],
                         "agent_id": agent_id,
                         "nature": alloc["nature"],
                         "montant": alloc.get("montant"),
                         "spent": alloc.get("spent"),
                         "reason": reason}
                to_release.append(entry)
                if not dry_run:
                    cat.conn.execute(
                        "UPDATE agent_budget_allocation SET status = 'closed', "
                        "updated_at = ? WHERE allocation_id = ?",
                        (_now(), alloc["allocation_id"]))
                    # rend la part libérée au budget_final (spent conserve la
                    # conso réelle ; l'allocation close libère la réservation).
                    released.append(entry)
            if not dry_run and released:
                cat.conn.commit()
        return {
            "ok": True,
            "total": total,
            "to_release": to_release,
            "released": released if not dry_run else [],
            "dry_run": dry_run,
        }
    except Exception:
        return {"ok": False, "total": 0, "to_release": [], "released": [],
                "dry_run": dry_run}


def run(cat=None, dry_run: bool = True) -> Dict[str, Any]:
    """Point d'entrée du tick réallocateur (à appeler périodiquement).

    `dry_run=False` pour libérer réellement. Best-effort."""
    from modules.sql.catalogue_repo import CatalogueDB
    cat = cat or CatalogueDB()
    return evaluate(cat, dry_run=dry_run)