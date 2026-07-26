"""Orchestrateur d'allocation LLM.

Fonction principale :
    allocate_llm(params: dict) -> dict

Pipeline :
    1. Lister les modèles candidats (key_endpoint_models available+declared)
    2. Filtrer par budget (check_budget pour chaque paire provider/model)
    3. Appliquer la stratégie (random, best-fallback, etc.)
    4. Retourner le résultat
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

from services.llm_allocation.strategies import (
    AllocationRequest,
    ModelOption,
    get_strategy,
    list_strategies,
)


def _get_catalogue() -> Any:
    """Retourne l'instance CatalogueDB."""
    from services.api._shared import _get_cat
    return _get_cat()


def _query_candidates() -> List[Dict[str, Any]]:
    """Liste les modèles disponibles (available=1, declared=1) depuis le catalogue.

    Retourne des lignes avec provider_ref, model_ref, provider_model_name,
    context_window, cost, et modality (pour détecter vision).
    """
    cat = _get_catalogue()
    if not cat:
        return []
    try:
        rows = cat.conn.execute("""
            SELECT
                cp.ref AS provider_ref,
                cm.ref AS model_ref,
                kem.provider_model_name,
                CAST(COALESCE(pm.context_window_effective, pm.context_window_tokens, 0) AS INTEGER) AS context_window,
                COALESCE(CAST(pm.cost_per_input_token AS REAL), 0.0) AS cost_per_input,
                COALESCE(CAST(pm.cost_per_output_token AS REAL), 0.0) AS cost_per_output,
                COALESCE(cm.modality, '') AS modality,
                COALESCE(pm.free_tier, 0) AS free_tier,
                kem.available,
                kem.declared,
                me.score_chat, me.score_coding, me.score_reasoning,
                me.score_knowledge, me.score_agentic, me.is_synthetic
            FROM key_endpoint_models kem
            JOIN catalogue_providers cp ON cp.id = kem.provider_id
            JOIN catalogue_models cm ON cm.id = kem.model_id
            JOIN provider_models pm ON pm.provider_id = kem.provider_id AND pm.model_id = kem.model_id
            LEFT JOIN model_efficacy me ON me.model_id = cm.id AND me.use_case = 'general'
            WHERE kem.available = 1 AND kem.declared = 1
              AND pm.status = 'active'
            ORDER BY cp.ref, cm.ref
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _has_key(provider_ref: str) -> bool:
    """Vérifie si une clé API est disponible pour ce provider."""
    try:
        from services.api._shared import _get_km
        km = _get_km()
        key = km.get_key(provider_ref)
        if key:
            return True
    except Exception:
        pass
    # Providers sans clé (ollama, local, builtin)
    try:
        cat = _get_catalogue()
        row = cat.conn.execute(
            "SELECT provider_type FROM catalogue_providers WHERE ref = ?",
            (provider_ref,)
        ).fetchone()
        if row and row["provider_type"] in ("ollama", "local", "builtin"):
            return True
    except Exception:
        pass
    return False


def _budget_ok(provider_ref: str, model_ref: str) -> bool:
    """Vérifie que le budget rate-limit n'est pas épuisé."""
    try:
        from services.tarif import check_budget
        result = check_budget(provider_ref, model_ref)
        return result.get("ok", True)
    except Exception:
        return True


def _has_vision(modality: str) -> bool:
    """Détecte si un modèle supporte la vision (images)."""
    if not modality:
        return False
    ml = modality.lower()
    return "image" in ml or "vision" in ml or "multimodal" in ml


def _build_candidates(raw_rows: List[Dict], request: AllocationRequest) -> List[ModelOption]:
    """Filtre les lignes brutes du catalogue en ModelOption prêtes pour la stratégie."""
    candidates: List[ModelOption] = []
    exclude_set = set(request.exclude or [])

    for row in raw_rows:
        ref = f"{row['provider_ref']}/{row['model_ref']}"
        if ref in exclude_set:
            continue

        # Vérifier clé API
        if not _has_key(row["provider_ref"]):
            continue

        # Filtrer par budget
        if not _budget_ok(row["provider_ref"], row["model_ref"]):
            continue

        # Filtrer par window minimum
        if request.min_window > 0:
            if row["context_window"] > 0 and row["context_window"] < request.min_window:
                continue

        # Filtrer par vision
        if request.needs_vision and not _has_vision(row["modality"]):
            continue

        # Filtrer par coût max
        if request.max_cost_per_call > 0:
            est_cost = (row["cost_per_input"] + row["cost_per_output"]) * request.min_window if request.min_window > 0 else 0
            if est_cost > request.max_cost_per_call:
                continue

        candidates.append(ModelOption(
            provider_ref=row["provider_ref"],
            model_ref=row["model_ref"],
            provider_model_name=row.get("provider_model_name", ""),
            context_window=row["context_window"],
            cost_per_input=row["cost_per_input"],
            cost_per_output=row["cost_per_output"],
            has_vision=_has_vision(row.get("modality", "")),
            budget_ok=True,
            key_available=True,
            score_chat=float(row.get("score_chat") or 0),
            score_coding=float(row.get("score_coding") or 0),
            score_reasoning=float(row.get("score_reasoning") or 0),
            score_knowledge=float(row.get("score_knowledge") or 0),
            score_agentic=float(row.get("score_agentic") or 0),
            is_synthetic=int(row.get("is_synthetic") or 0),
        ))

    return candidates


def allocate_llm(params: dict) -> dict:
    """Point d'entrée unique : alloue un modèle LLM selon la stratégie demandée.

    Paramètres (depuis AllocationRequest) :
        strategy    : str (random, best-fallback, eco, fast)
        task_type   : str (chat, coding, analysis, writing)
        min_window  : int (taille minimum de contexte)
        needs_vision: bool
        max_cost_per_call : float (coût max estimé en $)
        exclude     : list de str (refs à exclure, format "provider/model")
        agent_name  : str (pour contexte budget, optionnel)

    Retourne :
        {
            "status": "ok",
            "provider_ref": "...",
            "model_ref": "...",
            "strategy": "...",
            "reason": "...",
            "estimated_cost": {...},
            "candidates_count": N,
            "budget": {...}
        }
    """
    strategy_name = params.get("strategy", "random")
    request = AllocationRequest(
        strategy=strategy_name,
        task_type=params.get("task_type", "chat"),
        min_window=int(params.get("min_window", 0)),
        needs_vision=bool(params.get("needs_vision", False)),
        max_cost_per_call=float(params.get("max_cost_per_call", 0)),
        exclude=params.get("exclude", []),
        agent_name=params.get("agent_name", ""),
    )

    strategy_fn = get_strategy(strategy_name)
    if not strategy_fn:
        return {
            "status": "error",
            "error": f"stratégie inconnue: {strategy_name}",
            "available_strategies": list_strategies(),
        }

    # 1. Récupérer les candidats bruts du catalogue
    raw_rows = _query_candidates()
    if not raw_rows:
        return {
            "status": "error",
            "error": "aucun modèle disponible dans le catalogue",
            "hint": "vérifier que des clés API sont onboardées",
        }

    # 2. Filtrer (clé, budget, window, vision, coût)
    candidates = _build_candidates(raw_rows, request)
    if not candidates:
        return {
            "status": "error",
            "error": "aucun modèle disponible après filtrage",
            "hint": "vérifier budget, clés API, ou critères (window, vision)",
            "total_raw": len(raw_rows),
        }

    # 3. Appliquer la stratégie
    selected = strategy_fn(request, candidates)
    if not selected:
        return {
            "status": "error",
            "error": "la stratégie n'a retourné aucun modèle",
            "candidates_count": len(candidates),
        }

    return {
        "status": "ok",
        "provider_ref": selected.provider_ref,
        "model_ref": selected.model_ref,
        "provider_model_name": selected.provider_model_name,
        "strategy": strategy_name,
        "reason": f"sélectionné parmi {len(candidates)} modèles disponibles",
        "score": round(selected.score, 4),
        "candidates_count": len(candidates),
        "estimated_cost": {
            "per_input_token": selected.cost_per_input,
            "per_output_token": selected.cost_per_output,
        },
    }
