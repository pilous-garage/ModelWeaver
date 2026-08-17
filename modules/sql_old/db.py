#!/usr/bin/env python3
"""ModelWeaver — Data Access Layer (FAÇADE).

Issue #11 : modules/sql/db.py découpé par domaine. Ce fichier est maintenant
une façade qui ré-exporte tout depuis les modules par domaine pour préserver
la rétro-compatibilité (`from modules.sql.db import CatalogueDB` fonctionne
toujours).

Modules de domaine :
  - schema.py            : helpers de bas niveau (chemins DB, colonnes, versions)
  - catalogue_repo.py    : ProviderRepository, ModelRepository, KeyRepository,
                           LocalLLMRepository, LocalToolRepository,
                           CommandRepository, SystemStateRepository,
                           TursoCatalogueDB, CatalogueDB
  - modelweaver_repo.py  : AgentDBMixin, OrchestrationDBMixin, ModelWeaverDB
  - agents_repo.py       : WaitForRepository, AgentsDB
  - runtime_repo.py      : RuntimeDB

Aucune logique métier ici : tout est dans les modules de domaine.
"""

from modules.sql.schema import *  # noqa: F401,F403
from modules.sql.catalogue_repo import *  # noqa: F401,F403
from modules.sql.modelweaver_repo import *  # noqa: F401,F403
from modules.sql.agents_repo import *  # noqa: F401,F403
from modules.sql.runtime_repo import *  # noqa: F401,F403

# Helpers privés référencés par d'autres modules (sql_module, assign_classes,
# workspace…) — `import *` ne ré-exporte pas les noms commençant par _.
from modules.sql.schema import (  # noqa: F401
    _ref, _project_root, _default_local_db, _default_catalogue_db,
    _default_agents_db, _default_community_db, _default_user_db,
    _row_to_dict, _rows_to_list, _add_column_if_missing,
    _ensure_classes_outils_table, resolve_classe_id, _default_class_for_ref,
    read_db_version, read_meta, bump_meta,
)
from modules.sql.catalogue_repo import (  # noqa: F401
    _cols, fetch_remote_to_local,
)
