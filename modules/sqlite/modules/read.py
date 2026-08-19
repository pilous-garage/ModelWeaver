"""modules.read — lectures de modules.db (modules + module_routes)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db


def get_module(d: Db, path: str) -> Optional[Dict[str, Any]]:
    r = d._conn.execute(
        "SELECT * FROM modules WHERE path = ?", (path,)).fetchone()
    return dict(r) if r else None


def list_modules(d: Db, module_type: str = "", parent: str = "") -> List[Dict[str, Any]]:
    q = "SELECT * FROM modules WHERE 1=1"
    p: list = []
    if module_type:
        q += " AND type = ?"
        p.append(module_type)
    if parent:
        q += " AND parent = ?"
        p.append(parent)
    q += " ORDER BY path"
    return [dict(r) for r in d._conn.execute(q, p).fetchall()]


def list_routes(d: Db, module_path: str = "") -> List[Dict[str, Any]]:
    if module_path:
        rows = d._conn.execute(
            "SELECT * FROM module_routes WHERE module_path = ? "
            "ORDER BY name", (module_path,)).fetchall()
    else:
        rows = d._conn.execute(
            "SELECT * FROM module_routes ORDER BY module_path, name").fetchall()
    return [dict(r) for r in rows]


def get_route(d: Db, path: str) -> Optional[Dict[str, Any]]:
    r = d._conn.execute(
        "SELECT * FROM module_routes WHERE path = ?", (path,)).fetchone()
    return dict(r) if r else None


def find_routes(d: Db, pattern: str = "") -> List[Dict[str, Any]]:
    """Recherche par préfixe dotted (ex. 'modules.sqlite.' → tous les domaines)."""
    rows = d._conn.execute(
        "SELECT * FROM module_routes WHERE path LIKE ? ORDER BY path",
        (f"{pattern}%",)).fetchall()
    return [dict(r) for r in rows]


def call(d: Db, path: str, *args, **kwargs) -> Any:
    """Appelle une route déclarée : résolution via env.path (import réel) +
    vérification que la route est déclarée en base. Le path doit être
    le dotted complet (ou un alias env)."""
    route = get_route(d, path)
    if route is None:
        raise LookupError(f"route non déclarée dans modules.db: {path}")
    from modules.sqlite.env import path as P
    return P.call(path, *args, d=None, **kwargs)