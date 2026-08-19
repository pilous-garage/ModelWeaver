"""Shim compat: modules.sql.catalogue_genere -> modules.sql_old.catalogue_genere"""
from modules.sql_old.catalogue_genere import *  # noqa: F401,F403
from modules.sql_old import catalogue_genere as _mod
import sys as _sys
_sys.modules[__name__] = _sys.modules["modules.sql_old.catalogue_genere"]
