CONSUMES = {
    'modules.key_manager.key_manager': [
        'KeyLockedError',
        'KeyManager',
    ],
    'modules.llm_manager.base_bridge': [
        'BaseBridge',
        'BridgeError',
        'ChatResponse',
        'ErrorCategory',
        'ModelCapabilities',
    ],
    'modules.sql.db': [
        'CatalogueDB',
        'ModelWeaverDB',
    ],
    'modules.usage.usage_log': [
        'log_call',
    ],
    'services._common': [
        '_db_paths',
    ],
    'services.tarif': [
        'check_budget',
        'record_usage',
    ],
}
