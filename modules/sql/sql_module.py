"""Compat shim modules.sql.sql_module -> legacy ModelWeaverDB.

Redirige les imports historiques `modules.sql.sql_module.{ModelWeaverDB,
CatalogueDB, RuntimeDB, read_db_version}` vers le code legacy conservé dans
`modules/sql_old`. Le daemon legacy (handlers catalogue/catalogue_repo,
agents...) boote ainsi à côté des nouveaux domaines sqlite.

À terme, chaque handler utilisant ces singletons doit migrer vers
modules.sqlite.* puis ce shim disparaîtra.
"""
from modules.sql_old.agents_repo import AgentsDB
from modules.sql_old.catalogue_repo import CatalogueDB, ModelWeaverDB
from modules.sql_old.runtime_repo import RuntimeDB
from modules.sql_old.schema import read_db_version

__all__ = ["ModelWeaverDB", "CatalogueDB", "RuntimeDB", "AgentsDB", "read_db_version"]
