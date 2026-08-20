KIND = "module"
NAME = "llm_manager"
MODULE = "modules.llm_manager.llm_manager"
EXPORTS = [
    'LLMManager',
    # Bridges
    'BaseBridge',
    'ModelCapabilities', 'ChatResponse', 'BridgeError', 'ErrorCategory',
]

# Bridges disponibles (interface déclarative). Les appels passent par la
# façade LLMManager (config `llm.bridge`, défaut "direct") qui instancie
# l'un de ces bridges. litellm est retiré (legacy) : tout passe par
# DirectBridge (appels OpenAI-compatibles natifs).
BRIDGES = {
    "direct": {
        "module": "modules.llm_manager.direct_bridge",
        "class": "DirectBridge",
        "description": "DirectBridge — appels directs à l'API OpenAI-compatible (défaut)",
    },
}
