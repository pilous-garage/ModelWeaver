"""LLM résilient (timeout + repli).

Migré depuis services/skill_manager.py (_exec_timeout_llm).
"""

from modules.llm_manager.resilient import resilient_chat
from modules.llm_manager.base_bridge import BridgeError


def timeout(inputs: dict, ws: str) -> dict:
    """Appel LLM résilient : si le LLM ne répond pas dans `timeout`
    secondes (défaut 60), demande un autre LLM au gestionnaire de LLM
    (LLMManager.assign_llm) et réessaie."""
    provider_ref = inputs.get("provider_ref", "")
    model_ref = inputs.get("model_ref", "")
    if not provider_ref or not model_ref:
        return {"ok": False, "error": "provider_ref et model_ref requis"}
    prompt = inputs.get("prompt", "") or ""
    sys_p = inputs.get("system_prompt", "") or ""
    messages = inputs.get("messages") or []
    if not messages and prompt:
        messages = [{"role": "user", "content": prompt}]
    if not messages:
        return {"ok": False, "error": "messages ou prompt requis"}
    try:
        max_tokens = int(inputs.get("max_tokens", 1024))
        temperature = float(inputs.get("temperature", 0.7))
        timeout = int(inputs.get("timeout", 60))
    except (TypeError, ValueError) as e:
        return {"ok": False, "error": f"paramètre invalide: {e}"}
    fallback = bool(inputs.get("fallback", True))

    try:
        resp = resilient_chat(
            provider_ref, model_ref, messages,
            timeout=timeout, fallback=fallback,
            max_tokens=max_tokens, temperature=temperature,
            system_prompt=sys_p or None,
        )
    except BridgeError as e:
        return {"ok": False, "error": str(e),
                "category": getattr(e.category, "value", str(e.category)),
                "provider_ref": e.provider_ref, "model_ref": e.model_ref}
    except Exception as e:  # noqa
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "content": resp.content,
        "provider_used": getattr(resp, "provider_used", provider_ref),
        "model_used": getattr(resp, "model_used", model_ref),
        "fallbacks": getattr(resp, "fallbacks", 0),
    }


__skills__ = ["timeout"]
