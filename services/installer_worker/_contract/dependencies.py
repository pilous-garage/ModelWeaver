CONSUMES = {
    'modules.installer.recipe_parser': [
        'RecipeParser',
    ],
    'modules.sql.db': [
        'CatalogueDB',
        'ModelWeaverDB',
    ],
    'services._common': [
        'RECIPE_BASE',
        '_db_paths',
        '_quiet_stdout',
        'acquire_instance_lock',
        'log_to_file',
        'runtime_db_path',
    ],
    'services.depends': [
        'require_deps',
    ],
}
