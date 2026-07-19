CONSUMES = {
    'modules.key_manager.key_manager': [
        'KeyManager',
    ],
    'modules.llm_manager.base_bridge': [
        'BridgeError',
    ],
    'modules.llm_manager.litellm_bridge': [
        'LiteLLMBridge',
    ],
    'modules.sql.db': [
        'AgentsDB',
        'CatalogueDB',
        'ModelWeaverDB',
    ],
    'services._common': [
        'acquire_instance_lock',
        'mw_home',
    ],
    'services.lifecycle': [
        'HookType',
        'LifecycleManager',
        'get_event_bus',
    ],
    'services.ressource_manager.service': [
        'RessourceManager',
    ],
    'services.skill_manager': [
        'expand_workflow',
    ],
}
