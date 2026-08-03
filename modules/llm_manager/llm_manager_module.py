"""Interface publique du module `llm_manager` : orchestration LLM et bridges."""
from modules.llm_manager.llm_manager import LLMManager, seed_providers, seed_models, seed_provider_models
from modules.llm_manager.litellm_bridge import LiteLLMBridgeDefunct, ModelCapabilities, ChatResponse
from modules.llm_manager.base_bridge import BaseBridge, BridgeError, ErrorCategory
from modules.llm_manager.local_engines import get_local_engine_manager
from modules.llm_manager.catalogue_remote import fetch as catalogue_fetch, refresh_sync as catalogue_sync
from modules.llm_manager.bridges import BridgeRegistry

__all__ = [
    'LLMManager', 'seed_providers', 'seed_models', 'seed_provider_models',
    'LiteLLMBridgeDefunct', 'ModelCapabilities', 'ChatResponse',
    'BaseBridge', 'BridgeError', 'ErrorCategory',
    'get_local_engine_manager',
    'catalogue_fetch', 'catalogue_sync', 'BridgeRegistry',
]
