"""Interface publique du module `llm_manager` : orchestration LLM et bridges."""
from modules.llm_manager.llm_manager import LLMManager, seed_providers, seed_models, seed_provider_models
from modules.llm_manager.litellm_bridge import LiteLLMBridge, ModelCapabilities, ChatResponse
from modules.llm_manager.base_bridge import BaseBridge, BridgeError, ErrorCategory
from modules.llm_manager.local_engines import get_local_engine_manager

__all__ = [
    'LLMManager', 'seed_providers', 'seed_models', 'seed_provider_models',
    'LiteLLMBridge', 'ModelCapabilities', 'ChatResponse',
    'BaseBridge', 'BridgeError', 'ErrorCategory',
    'get_local_engine_manager',
]
