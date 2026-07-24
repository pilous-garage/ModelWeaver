from modules.llm_manager.litellm_bridge import LiteLLMBridge
from modules.llm_manager.base_bridge import BridgeError


def _bridge():
    try:
        from modules.sql.db import CatalogueDB, ModelWeaverDB
        from modules.key_manager.key_manager import KeyManager
        return LiteLLMBridge(cat=CatalogueDB(), km=KeyManager(ModelWeaverDB()))
    except ImportError:
        return LiteLLMBridge()


def llm_call(inputs: dict, ws: str) -> dict:
    provider = inputs.get("provider_ref", "")
    model = inputs.get("model_ref", "")
    messages = inputs.get("messages", [])
    temperature = inputs.get("temperature", 0.7)
    max_tokens = inputs.get("max_tokens", 4096)
    if not provider or not model:
        return {"result": "", "error": "provider_ref et model_ref requis",
                "ok": False}
    try:
        bridge = _bridge()
        response = bridge.chat(
            provider_ref=provider, model_ref=model,
            messages=messages, temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.content if hasattr(response, 'content') else str(response)
        tokens = 0
        if hasattr(response, 'usage') and isinstance(response.usage, dict):
            tokens = response.usage.get("total_tokens", 0)
        return {"result": content, "tokens": tokens, "ok": True}
    except BridgeError as e:
        return {"result": "", "error": str(e), "ok": False}
    except Exception as e:
        return {"result": "", "error": str(e), "ok": False}


__skills__ = ["llm_call"]
