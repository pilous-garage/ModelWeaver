"""Compat shim modules.sql.db → modules.sqlite"""

from modules.sqlite.base import Db
from modules.sqlite.local import db as CatalogueDB
from modules.sqlite.runtime_llm import db as RuntimeDB

ModelWeaverDB = Db

__all__ = ["Db", "CatalogueDB", "RuntimeDB", "ModelWeaverDB"]
