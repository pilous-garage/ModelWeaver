"""env.write — écritures du domaine env (env vars, paths, alias)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from modules.sqlite.base import Db


def set_env(d: Db, name: str, value: str, scope: str = "user",
            description: str = "", create: bool = True) -> Dict[str, Any]:
    """Persiste une variable d'environnement (ne touche pas os.environ :
    la priorité de lecture reste os.environ > env_var)."""
    d._conn.execute(
        "INSERT INTO env_var (name, value, scope, description, updated_at) "
        "VALUES (?,?,?,?, datetime('now')) "
        "ON CONFLICT(name) DO UPDATE SET value=excluded.value, "
        "scope=excluded.scope, description=excluded.description, "
        "updated_at=datetime('now')",
        (name, str(value), scope, description))
    d._conn.commit()
    return {"ok": True, "name": name, "value": str(value)}


def unset_env(d: Db, name: str) -> Dict[str, Any]:
    d._conn.execute("DELETE FROM env_var WHERE name = ?", (name,))
    d._conn.commit()
    return {"ok": True, "name": name}


def register_path(d: Db, path: str, entry_type: str = "module",
                  parent: str = "", ref: str = "", description: str = ""
                  ) -> Dict[str, Any]:
    """Annuaire des chemins canoniques (idempotent)."""
    d._conn.execute(
        "INSERT INTO path (entry_type, path, parent, ref, description, active) "
        "VALUES (?,?,?,?,?,1) "
        "ON CONFLICT(path) DO UPDATE SET entry_type=excluded.entry_type, "
        "parent=excluded.parent, ref=excluded.ref, "
        "description=excluded.description, active=1",
        (entry_type, path, parent, ref, description))
    d._conn.commit()
    return {"ok": True, "path": path}


def unregister_path(d: Db, path: str) -> Dict[str, Any]:
    d._conn.execute("UPDATE path SET active = 0 WHERE path = ?", (path,))
    d._conn.commit()
    return {"ok": True, "path": path}


def set_alias(d: Db, alias: str, target: str, kind: str = "path",
              description: str = "") -> Dict[str, Any]:
    """Raccourci → cible. Idempotent (upsert)."""
    d._conn.execute(
        "INSERT INTO alias (alias, target, kind, description, created_at) "
        "VALUES (?,?,?,?, datetime('now')) "
        "ON CONFLICT(alias) DO UPDATE SET target=excluded.target, "
        "kind=excluded.kind, description=excluded.description",
        (alias, target, kind, description))
    d._conn.commit()
    return {"ok": True, "alias": alias, "target": target}


def unset_alias(d: Db, alias: str) -> Dict[str, Any]:
    d._conn.execute("DELETE FROM alias WHERE alias = ?", (alias,))
    d._conn.commit()
    return {"ok": True, "alias": alias}