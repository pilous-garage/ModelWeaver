"""Route LLM Allocation — llm/allocate.

Délègue à services.llm_allocation.allocate.allocate_llm().
"""

from services.api.router import register
from services.llm_allocation.allocate import allocate_llm


def op_llm_allocate(params):
    """Alloue un modèle LLM selon la stratégie demandée.

    Corps (JSON) :
        strategy     : "random" (défaut) | "best-fallback" | "eco" | "fast"
        task_type    : "chat" | "coding" | "analysis" | "writing"
        min_window   : int (taille contexte minimum)
        needs_vision : bool
        max_cost_per_call : float
        exclude      : [str] (refs "provider/model" à exclure)
        agent_name   : str
        latence_penalise : float (s) — seuil latence sans pénalité (défaut 1.0)
        latence_regule   : float (s) — échelle de pénalité (défaut 60.0)
        (une tâche tolérante peut passer 60/300 pour garder les LLM lents)

    Retourne :
        {
            "status": "ok",
            "provider_ref": "openrouter",
            "model_ref": "openai/gpt-4o-mini",
            "strategy": "random",
            "reason": "sélectionné parmi 24 modèles disponibles",
            "candidates_count": 24,
            "estimated_cost": {...}
        }
    """
    return allocate_llm(params)


register("llm/allocate", op_llm_allocate)
