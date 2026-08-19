"""Shim de compatibilité modules.sql → modules.sqlite

Ce module redirige les imports historiques `modules.sql.db.*` vers les nouveaux domaines sqlite.
À terme, tous les imports doivent être migrés vers modules.sqlite.
"""

from modules.sqlite.base import Db as Db
from modules.sqlite.local import db as CatalogueDB
from modules.sqlite.runtime_llm import db as RuntimeDB

ModelWeaverDB = Db

__all__ = ["Db", "CatalogueDB", "RuntimeDB", "ModelWeaverDB"]
