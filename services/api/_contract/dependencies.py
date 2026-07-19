CONSUMES = {
    'modules.checker.checker': [
        'Checker',
    ],
    'modules.key_manager.key_manager': [
        'KeyLockedError',
        'KeyManager',
    ],
    'modules.key_manager.onboarder': [
        'Onboarder',
    ],
    'modules.llm_manager.base_bridge': [
        'BridgeError',
        'ErrorCategory',
    ],
    'modules.llm_manager.litellm_bridge': [
        'LiteLLMBridge',
    ],
    'modules.llm_manager.llm_manager': [
        'LLMManager',
        'seed_models',
        'seed_provider_models',
        'seed_providers',
    ],
    'modules.llm_manager.local_engines': [
        'get_local_engine_manager',
    ],
    'modules.sql.db': [
        'AgentsDB',
        'CatalogueDB',
        'ModelWeaverDB',
        'RuntimeDB',
        '_default_class_for_ref',
        '_ensure_classes_outils_table',
        'fetch_remote_to_local',
        'read_db_version',
        'resolve_classe_id',
    ],
    'modules.system': [
        'deps',
    ],
    'modules.system.deps': [
        'install_system_package',
        'install_target_dependencies',
    ],
    'modules.usage.budget': [
        'get_budget_rows',
        'get_budget_summary',
        'get_free_tier_models',
    ],
    'services._common': [
        '_db_paths',
        '_quiet_stdout',
        'acquire_instance_lock',
        'log_to_file',
        'mw_home',
        'runtime_db_path',
    ],
    'services.afd.ipc': [
        'AFDSocketClient',
    ],
    'services.agent_daemon': [
        'AgentDaemon',
    ],
    'services.agent_manager.service': [
        'AgentManager',
    ],
    'services.audit': [
        'audit',
    ],
    'services.depends': [
        'check_all_units',
    ],
    'services.fs_auth': [
        'FsAuthManager',
    ],
    'services.installer_worker': [
        'jobs',
    ],
    'services.installer_worker.jobs': [
        'enqueue_job',
        'ensure_install_jobs',
        'install_tool',
        'list_jobs',
        'uninstall_tool',
    ],
    'services.logger': [
        'MWLogger',
    ],
    'services.ratelimit': [
        'check_rate_limit',
    ],
    'services.tarif': [
        '_get',
        'check_budget',
        'sync_tarif',
        'tarif_info',
    ],
    'services.watch_sysstate': [
        'service',
    ],
    'services.watch_sysstate.service': [
        'get_system_state',
    ],
}
