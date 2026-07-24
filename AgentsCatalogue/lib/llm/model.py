from modules.llm_manager.litellm_bridge import LiteLLMBridge


def _bridge():
    try:
        from modules.sql.db import CatalogueDB, ModelWeaverDB
        from modules.key_manager.key_manager import KeyManager
        return LiteLLMBridge(cat=CatalogueDB(), km=KeyManager(ModelWeaverDB()))
    except ImportError:
        return LiteLLMBridge()


def llm_model(inputs: dict, ws: str) -> dict:
    provider = inputs.get("provider_ref", "")
    model = inputs.get("model_ref", "")
    caps = {}
    if provider and model:
        try:
            bridge = _bridge()
            c = bridge.get_capabilities(provider, model)
            caps = {
                "context_window": c.context_window,
                "max_output": c.max_output,
                "cost_input_per_1k": c.cost_input_per_1k,
                "cost_output_per_1k": c.cost_output_per_1k,
                "supports_vision": c.supports_vision,
                "supports_function_calling": c.supports_function_calling,
            }
        except Exception:
            caps = {}
    return {"provider": provider, "model": model,
            "capabilities": caps, "ok": True}


__skills__ = ["llm_model"]
