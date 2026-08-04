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

# Helpers privés encore référencés par certains modules (workspace.py…)
from modules.sql.schema import _add_column_if_missing  # noqa: F401
from modules.sql.schema import _row_to_dict, _rows_to_list  # noqa: F401
