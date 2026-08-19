"""Shim compat: modules.sql.agent_repository -> modules.sql_old.agent_repository"""
from modules.sql_old.agent_repository import *  # noqa: F401,F403
from modules.sql_old import agent_repository as _mod
import sys as _sys
_sys.modules[__name__] = _sys.modules["modules.sql_old.agent_repository"]
