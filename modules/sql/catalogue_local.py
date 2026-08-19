"""Shim compat: modules.sql.catalogue_local -> modules.sql_old.catalogue_local"""
from modules.sql_old.catalogue_local import *  # noqa: F401,F403
from modules.sql_old import catalogue_local as _mod
import sys as _sys
_sys.modules[__name__] = _sys.modules["modules.sql_old.catalogue_local"]
