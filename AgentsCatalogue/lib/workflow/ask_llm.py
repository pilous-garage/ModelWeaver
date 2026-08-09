"""ask_llm — alloue un LLM à l'agent via LLMManager.assign_llm.

use_case="coding" exige function_calling (donc un modèle agentic). Le FSM
capture provider_ref/model_ref dans ses variables (_llm_provider/_llm_model)
et les réutilise pour les steps llm_call suivants.
"""

import re


def _agent_id_from_home(home: str) -> str:
    m = re.search(r"agent_home/(\d+)", home or "")
    return m.group(1) if m else ""


def _resolve_use_case(use_case: str, min_score: float) -> str:
    """coding_high = coding avec un seuil de score élevé."""
    if use_case in ("coding_high",):
        return "coding"
    return use_case or "coding"


def exec(inputs: dict, home: str) -> dict:
    from modules.llm_manager.llm_manager import LLMManager
    from modules.sql.catalogue_repo import CatalogueDB

    use_case = _resolve_use_case(inputs.get("use_case", "coding"),
                                 float(inputs.get("min_score", 0) or 0))
    agent_id = inputs.get("agent_id", "") or _agent_id_from_home(home)
    min_window = int(inputs.get("min_window", 0) or 0)
    min_score = float(inputs.get("min_score", 0) or 0)

    try:
        llm_mgr = LLMManager(CatalogueDB())
        llm = llm_mgr.assign_llm(use_case=use_case, agent_id=agent_id or None,
                                 min_window=min_window)
    except Exception as e:
        return {"ok": False, "provider_ref": "", "model_ref": "",
                "use_case": use_case, "error": f"assign_llm: {e}"}
    if not llm:
        return {"ok": False, "provider_ref": "", "model_ref": "",
                "use_case": use_case,
                "error": f"aucun modèle disponible pour use_case={use_case}"}
    p_ref = llm.get("provider_ref", "")
    m_ref = llm.get("model_ref", "")
    if not p_ref or not m_ref:
        return {"ok": False, "provider_ref": p_ref, "model_ref": m_ref,
                "use_case": use_case,
                "error": "assign_llm a retourné provider/model vides"}
    return {"ok": True, "provider_ref": p_ref, "model_ref": m_ref,
            "use_case": use_case}


__skills__ = ["exec"]
