"""Interface publique du module `sql` : couche d'accès aux données."""
from modules.sql.db import (
    ModelWeaverDB, CatalogueDB, TursoCatalogueDB, RuntimeDB,
    read_db_version, fetch_remote_to_local,
    _ensure_classes_outils_table, resolve_classe_id, _default_class_for_ref,
)
from modules.sql.agents_repo import AgentsDB

__all__ = [
    'ModelWeaverDB', 'CatalogueDB', 'TursoCatalogueDB', 'RuntimeDB', 'AgentsDB',
    'read_db_version', 'fetch_remote_to_local',
    '_ensure_classes_outils_table', 'resolve_classe_id', '_default_class_for_ref',
]
