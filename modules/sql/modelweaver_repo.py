"""Shim compat: modules.sql.modelweaver_repo -> modules.sql_old.modelweaver_repo"""
from modules.sql_old.modelweaver_repo import *  # noqa: F401,F403
from modules.sql_old import modelweaver_repo as _mod
import sys as _sys
_sys.modules[__name__] = _sys.modules["modules.sql_old.modelweaver_repo"]
