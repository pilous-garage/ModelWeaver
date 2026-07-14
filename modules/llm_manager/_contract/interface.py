KIND = "module"
NAME = "llm_manager"
MODULE = "modules.llm_manager.llm_manager"
EXPORTS = [
    'LLMManager',
    # Bridges
    'BaseBridge', 'LiteLLMBridge',
    'ModelCapabilities', 'ChatResponse', 'BridgeError', 'ErrorCategory',
]

# Bridges disponibles (interface déclarative)
BRIDGES = {
    "litellm": {
        "module": "modules.llm_manager.litellm_bridge",
        "class": "LiteLLMBridge",
        "description": "LiteLLM — Tous providers cloud + OpenAI-compatible locaux",
    },
    # Exemple pour un bridge custom plus tard :
    # "openai_direct": {
    #     "module": "modules.llm_manager.bridges.openai_direct",
    #     "class": "OpenAIDirectBridge",
    #     "description": "OpenAI SDK direct (sans LiteLLM)",
    # },
}
