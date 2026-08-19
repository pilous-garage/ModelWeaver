"""Shim compat: modules.sql.migrations -> modules.sql_old.migrations"""
from modules.sql_old.migrations import *  # noqa: F401,F403
