from modules.llm_manager.llm_manager import LLMManager


def _bridge():
    try:
        from modules.sql.db import CatalogueDB, ModelWeaverDB
        from modules.key_manager.key_manager import KeyManager
        return LLMManager(cat=CatalogueDB(),
                          km=KeyManager(ModelWeaverDB())).get_bridge()
    except ImportError:
        return LLMManager(cat=None).get_bridge()


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
