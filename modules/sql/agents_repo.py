"""Shim compat: modules.sql.agents_repo -> modules.sql_old.agents_repo"""
from modules.sql_old.agents_repo import *  # noqa: F401,F403
from modules.sql_old import agents_repo as _mod
import sys as _sys
_sys.modules[__name__] = _sys.modules["modules.sql_old.agents_repo"]
