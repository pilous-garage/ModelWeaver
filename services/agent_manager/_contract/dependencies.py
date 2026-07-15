"""Dépendances du service `agent_manager`."""
from modules.sql.db import AgentsDB
from modules.llm_manager.litellm_bridge import LiteLLMBridge

CONSUMES = {
    "modules.sql.db": ["AgentsDB"],
    "modules.llm_manager.litellm_bridge": ["LiteLLMBridge"],
    "modules.llm_manager.base_bridge": ["BridgeError"],
    "AgentFrameWork.fsm_interpreter": ["FSMInterpreter", "FSMResult"],
    "services._common": ["mw_home", "acquire_instance_lock"],
}
