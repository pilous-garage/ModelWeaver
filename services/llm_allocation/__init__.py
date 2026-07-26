"""LLM Allocation — allocation stratégique de modèles LLM.

Point d'entrée unique :
    from services.llm_allocation.allocate import allocate_llm
    result = allocate_llm({"strategy": "random", "task_type": "chat", ...})

Stratégies disponibles :
    - random        : choisit au hasard parmi les modèles avec budget restant
    - best-fallback : score + fallback chain si le premier échoue
    - eco           : moindre coût (TODO)
    - fast          : moindre latence (TODO)
"""
from services.llm_allocation.allocate import allocate_llm
