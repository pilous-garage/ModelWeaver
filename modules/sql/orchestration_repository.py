"""Shim compat: modules.sql.orchestration_repository -> modules.sql_old.orchestration_repository"""
from modules.sql_old.orchestration_repository import *  # noqa: F401,F403
from modules.sql_old import orchestration_repository as _mod
import sys as _sys
_sys.modules[__name__] = _sys.modules["modules.sql_old.orchestration_repository"]
