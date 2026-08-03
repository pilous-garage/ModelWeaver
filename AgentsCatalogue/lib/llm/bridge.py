from modules.llm_manager.llm_manager import LLMManager


def _bridge():
    """Bridge avec catalogue + key manager (configuration agent)."""
    try:
        from modules.sql.db import CatalogueDB, ModelWeaverDB
        from modules.key_manager.key_manager import KeyManager
        return LLMManager(cat=CatalogueDB(),
                          km=KeyManager(ModelWeaverDB())).get_bridge()
    except ImportError:
        return LLMManager(cat=None).get_bridge()


def capabilities(inputs: dict, ws: str) -> dict:
    provider = inputs.get("provider_ref", "")
    model = inputs.get("model_ref", "")
    if not provider or not model:
        return {"ok": False, "error": "provider_ref et model_ref requis"}
    try:
        c = _bridge().get_capabilities(provider, model)
        return {
            "provider": provider,
            "model": model,
            "context_window": c.context_window,
            "max_output": c.max_output,
            "cost_input_per_1k": c.cost_input_per_1k,
            "cost_output_per_1k": c.cost_output_per_1k,
            "supports_vision": c.supports_vision,
            "supports_function_calling": c.supports_function_calling,
            "ok": True,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_providers(inputs: dict, ws: str) -> dict:
    try:
        providers = _bridge().list_available_providers()
        return {"providers": providers, "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_models(inputs: dict, ws: str) -> dict:
    provider = inputs.get("provider_ref", "")
    try:
        models = _bridge().list_available_models(provider or None)
        return {"models": models, "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def health(inputs: dict, ws: str) -> dict:
    provider = inputs.get("provider_ref", "")
    model = inputs.get("model_ref", "")
    try:
        h = _bridge().health_check(provider, model)
        return {"healthy": h, "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["capabilities", "list_providers", "list_models", "health"]
