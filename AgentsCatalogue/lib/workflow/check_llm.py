"""check_llm — vérifie qu'un LLM est assigné à l'agent (sans allouer).

Le FSM résout `{{_llm_provider}}` / `{{_llm_model}}` depuis ses variables.
Si la variable n'existe pas, le placeholder reste littéral (ex. `{{_llm_provider}}`)
→ on considère qu'aucun LLM n'est assigné. Le FSM branche alors sur ask_llm.
"""

_PLACEHOLDER_MARKERS = ("{{", "}}", "undefined", "none", "None")


def _looks_unset(value: str) -> bool:
    if not value:
        return True
    v = str(value).strip()
    if not v or v.lower() == "none":
        return True
    return any(m in v for m in _PLACEHOLDER_MARKERS)


def exec(inputs: dict, home: str) -> dict:
    p_ref = str(inputs.get("provider_ref", "") or "")
    m_ref = str(inputs.get("model_ref", "") or "")
    has_llm = (not _looks_unset(p_ref)) and (not _looks_unset(m_ref))
    if not has_llm:
        return {"ok": False, "has_llm": False,
                "provider_ref": "", "model_ref": "",
                "error": "aucun LLM assigné (provider/model manquants)"}
    return {"ok": True, "has_llm": True,
            "provider_ref": p_ref, "model_ref": m_ref}


__skills__ = ["exec"]
