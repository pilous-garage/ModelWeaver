KIND = "module"
NAME = "llm_manager"
MODULE = "modules.llm_manager.llm_manager"
EXPORTS = [
    'LLMManager',
    # Bridges
    'BaseBridge', 'LiteLLMBridgeDefunct',
    'ModelCapabilities', 'ChatResponse', 'BridgeError', 'ErrorCategory',
]

# Bridges disponibles (interface déclarative). Les appels passent par la
# façade LLMManager (config `llm.bridge`, défaut "direct") qui instancie
# l'un de ces bridges.
BRIDGES = {
    "direct": {
        "module": "modules.llm_manager.direct_bridge",
        "class": "DirectBridge",
        "description": "DirectBridge — appels directs à l'API OpenAI-compatible (défaut)",
    },
    "litellm": {
        "module": "modules.llm_manager.litellm_bridge",
        "class": "LiteLLMBridgeDefunct",
        "description": "LiteLLM — tous providers cloud (legacy, non défaut)",
    },
}
