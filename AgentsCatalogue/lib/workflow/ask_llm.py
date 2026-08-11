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
    # restrict_llm : allowlist de modèles → exclusions (tout le reste).
    # exclusions explicites (exclude_models) ajoutées.
    exclude_models = list(inputs.get("exclude_models") or [])
    allow = inputs.get("restrict_llm") or []
    if isinstance(allow, str):
        allow = [m.strip() for m in allow.split(",") if m.strip()]
    # Si non fourni dans inputs, relire depuis les variables de l'agent BDD
    # (injectées par dev-chat / swarm-as-llm via _run_pilot).
    if not exclude_models and not allow:
        try:
            from modules.sql.agents_repo import AgentsDB
            import json as _json
            _aid = _agent_id_from_home(home)
            if _aid:
                db = AgentsDB()
                row = db.conn.execute(
                    "SELECT variables_json FROM agents WHERE agent_id = ?",
                    (int(_aid),)).fetchone()
                db.close()
                if row:
                    _vars = _json.loads(row["variables_json"] or "{}")
                    exclude_models = list(_vars.get("exclude_models") or [])
                    allow = _vars.get("restrict_llm") or []
                    if isinstance(allow, str):
                        allow = [m.strip() for m in allow.split(",") if m.strip()]
        except Exception:
            pass
    if allow:
        try:
            from modules.sql.catalogue_repo import CatalogueDB
            cat = CatalogueDB()
            all_models = [r["ref"] for r in cat.conn.execute(
                "SELECT ref FROM provider_models").fetchall()]
            exclude_models = list(set(exclude_models) |
                                  (set(all_models) - set(allow)))
        except Exception:
            pass

    try:
        llm_mgr = LLMManager(CatalogueDB())
        llm = llm_mgr.assign_llm(use_case=use_case, agent_id=agent_id or None,
                                 min_window=min_window,
                                 exclude_models=exclude_models or None)
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
