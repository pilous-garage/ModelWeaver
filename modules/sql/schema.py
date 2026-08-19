"""Shim compat: modules.sql.schema -> modules.sql_old.schema"""
from modules.sql_old.schema import *  # noqa: F401,F403
from modules.sql_old import schema as _mod
import sys as _sys
_sys.modules[__name__] = _sys.modules["modules.sql_old.schema"]
