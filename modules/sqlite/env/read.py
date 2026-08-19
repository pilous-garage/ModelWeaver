"""env.read — lectures du domaine env (env vars, paths, alias)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db


def get_env(d: Db, name: str, default: Any = None) -> Any:
    """Valeur d'une variable : os.environ d'abord, sinon env.db puis default."""
    if name in __import__("os").environ:
        return __import__("os").environ[name]
    r = d._conn.execute(
        "SELECT value FROM env_var WHERE name = ?", (name,)).fetchone()
    return r["value"] if r else default


def list_env(d: Db, scope: str = "") -> List[Dict[str, Any]]:
    if scope:
        rows = d._conn.execute(
            "SELECT * FROM env_var WHERE scope = ? ORDER BY name", (scope,)).fetchall()
    else:
        rows = d._conn.execute("SELECT * FROM env_var ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def list_paths(d: Db, entry_type: str = "") -> List[Dict[str, Any]]:
    if entry_type:
        rows = d._conn.execute(
            "SELECT * FROM path WHERE entry_type = ? AND active = 1 "
            "ORDER BY path", (entry_type,)).fetchall()
    else:
        rows = d._conn.execute(
            "SELECT * FROM path WHERE active = 1 ORDER BY path").fetchall()
    return [dict(r) for r in rows]


def get_path(d: Db, path: str) -> Optional[Dict[str, Any]]:
    r = d._conn.execute(
        "SELECT * FROM path WHERE path = ? AND active = 1", (path,)).fetchone()
    return dict(r) if r else None


def get_alias(d: Db, alias: str) -> Optional[Dict[str, Any]]:
    r = d._conn.execute(
        "SELECT * FROM alias WHERE alias = ?", (alias,)).fetchone()
    return dict(r) if r else None


def list_aliases(d: Db) -> List[Dict[str, Any]]:
    return [dict(r) for r in d._conn.execute(
        "SELECT * FROM alias ORDER BY alias").fetchall()]


def resolve_alias(d: Db, alias: str) -> Optional[str]:
    r = d._conn.execute(
        "SELECT target FROM alias WHERE alias = ?", (alias,)).fetchone()
    return r["target"] if r else None