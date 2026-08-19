"""modules.write — écritures modules.db + auto-découverte.

L'auto-découverte (discover) :
  - énumère les .py sous modules/ (type module), services/ (service),
    modules/sqlite/ (domain) ;
  - tente d'IMPORTER chaque module en try/except : les modules importables
    passent status='loaded' et leurs FONCTIONS PUBLIQUES (pas de '_' préfixe,
    définies dans le fichier) sont déclarées en module_routes ;
  - les modules non importables restent 'declared' (routes vides) — la
    déclaration reste, l'import d'échec n'empêche pas la liste.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from typing import Any, Dict

from modules.sqlite.base import Db
from modules.sqlite.env import path as P


def register_module(d: Db, path: str, module_type: str = "module",
                    parent: str = "", description: str = "",
                    version: str = "") -> Dict[str, Any]:
    name = path.split(".")[-1]
    if not parent:
        parent = ".".join(path.split(".")[:-1])
    d._conn.execute(
        "INSERT INTO modules (path, name, type, parent, description, status, "
        "version, updated_at) VALUES (?,?,?,?,?, 'declared', ?, datetime('now')) "
        "ON CONFLICT(path) DO UPDATE SET type=excluded.type, "
        "parent=excluded.parent, description=excluded.description, "
        "version=excluded.version, updated_at=datetime('now')",
        (path, name, module_type, parent, description, version))
    d._conn.commit()
    return {"ok": True, "path": path, "name": name}


def register_route(d: Db, module_path: str, name: str, signature: str = "",
                   description: str = "", callable_path: str = "",
                   kind: str = "func") -> Dict[str, Any]:
    route_path = callable_path or f"{module_path}.{name}"
    d._conn.execute(
        "INSERT INTO module_routes (module_path, name, path, callable_path, "
        "signature, description, kind, updated_at) "
        "VALUES (?,?,?,?,?,?,?, datetime('now')) "
        "ON CONFLICT(path) DO UPDATE SET signature=excluded.signature, "
        "description=excluded.description, callable_path=excluded.callable_path, "
        "updated_at=datetime('now')",
        (module_path, name, route_path, callable_path, signature, description, kind))
    d._conn.commit()
    return {"ok": True, "path": route_path}


def clear_routes(d: Db, module_path: str) -> None:
    d._conn.execute("DELETE FROM module_routes WHERE module_path = ?",
                    (module_path,))
    d._conn.commit()


def unregister_module(d: Db, path: str) -> Dict[str, Any]:
    d._conn.execute("DELETE FROM module_routes WHERE module_path = ?", (path,))
    d._conn.execute("DELETE FROM modules WHERE path = ?", (path,))
    d._conn.commit()
    return {"ok": True, "path": path}


def _module_functions(mod) -> Dict[str, Any]:
    """Fonctions publiques définies DANS ce module (pas d'imports)."""
    out = {}
    for name, obj in inspect.getmembers(mod, inspect.isfunction):
        if name.startswith("_"):
            continue
        try:
            if obj.__module__ != mod.__name__:
                continue
        except Exception:
            continue
        sig = str(inspect.signature(obj)) if "signature" in dir(inspect) else ""
        out[name] = sig
    return out


def discover(d: Db, roots=None, clear_first: bool = False) -> Dict[str, Any]:
    """Auto-découverte : enregistre modules + routes publiques depuis le repo."""
    nodes = P.discover(roots)["nodes"]
    total = loaded = 0
    for n in nodes:
        mod_path = n["path"]
        register_module(d, mod_path, module_type=n["type"])
        total += 1
        if clear_first:
            clear_routes(d, mod_path)
        try:
            mod = importlib.import_module(mod_path)
        except ImportError:
            continue  # reste 'declared' (routes vides)
        except Exception:
            continue
        d._conn.execute(
            "UPDATE modules SET status='loaded', updated_at=datetime('now') "
            "WHERE path = ?", (mod_path,))
        loaded += 1
        for fname, sig in _module_functions(mod).items():
            register_route(d, mod_path, fname, signature=sig)
    d._conn.commit()
    return {"ok": True, "total": total, "loaded": loaded}