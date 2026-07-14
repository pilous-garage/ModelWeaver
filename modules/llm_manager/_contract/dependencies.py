CONSUMES = {
    'modules.sql.db': ['ModelWeaverDB', 'CatalogueDB'],
    'modules.llm_manager.base_bridge': [
        'BaseBridge', 'ModelCapabilities', 'ChatResponse',
        'ErrorCategory', 'BridgeError',
    ],
}
