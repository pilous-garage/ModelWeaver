"""Dépendances du service `installer_worker`."""
from modules.sql.db import ModelWeaverDB, CatalogueDB
from modules.installer.installer import Installer

CONSUMES = {
    "modules.sql.db": ["ModelWeaverDB", "CatalogueDB"],
    "modules.installer.installer": ["Installer"],
    "services._common": ["log_to_file", "acquire_instance_lock", "RECIPE_BASE", "_db_paths", "_quiet_stdout"],
    "services.depends": ["require_deps"],
}
