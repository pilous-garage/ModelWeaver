"""Dépendances du service `watch_installed`."""
from modules.sql.db import ModelWeaverDB

CONSUMES = {
    "modules.sql.db": ["ModelWeaverDB"],
    "services._common": ["acquire_instance_lock", "_db_paths"],
}
