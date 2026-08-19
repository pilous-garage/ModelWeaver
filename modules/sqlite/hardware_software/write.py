"""hardware_software.write — écritures hardware_software.db."""

from __future__ import annotations

from typing import Any, Dict

from modules.sqlite.base import Db


def register_software(d: Db, tool_ref: str, name: str, recipe_ref: str = "",
                      cmd_json: str = "{}", path_installed: str = "",
                      status: str = "installed", privilege_tag: str = "default",
                      privilege_override: str = "") -> Dict[str, Any]:
    """Déclare un logiciel installé (idempotent sur tool_ref)."""
    d._conn.execute(
        "INSERT INTO installed_software "
        "(tool_ref, name, recipe_ref, cmd_json, path_installed, status, "
        "privilege_tag, privilege_override, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?, datetime('now'), datetime('now')) "
        "ON CONFLICT(tool_ref) DO UPDATE SET "
        "name=excluded.name, recipe_ref=excluded.recipe_ref, "
        "cmd_json=excluded.cmd_json, path_installed=excluded.path_installed, "
        "status=excluded.status, privilege_tag=excluded.privilege_tag, "
        "privilege_override=excluded.privilege_override, "
        "updated_at=datetime('now')",
        (tool_ref, name, recipe_ref, cmd_json, path_installed, status,
         privilege_tag, privilege_override))
    d._conn.commit()
    return {"ok": True, "tool_ref": tool_ref}


def set_status(d: Db, tool_ref: str, status: str) -> Dict[str, Any]:
    d._conn.execute(
        "UPDATE installed_software SET status = ?, updated_at = datetime('now') "
        "WHERE tool_ref = ?", (status, tool_ref))
    d._conn.commit()
    return {"ok": True, "tool_ref": tool_ref, "status": status}


def set_privilege(d: Db, tool_ref: str, privilege_override: str) -> Dict[str, Any]:
    """Override utilisateur du privilège (écrase le tag par défaut)."""
    d._conn.execute(
        "UPDATE installed_software SET privilege_override = ?, "
        "updated_at = datetime('now') WHERE tool_ref = ?",
        (privilege_override, tool_ref))
    d._conn.commit()
    return {"ok": True, "tool_ref": tool_ref,
            "privilege_override": privilege_override}


def unregister_software(d: Db, tool_ref: str) -> Dict[str, Any]:
    d._conn.execute(
        "DELETE FROM installed_software WHERE tool_ref = ?", (tool_ref,))
    d._conn.commit()
    return {"ok": True, "tool_ref": tool_ref}


def add_command(d: Db, tool_ref: str, command_name: str, cmd_json: str = "{}",
                privilege_tag: str = "default") -> Dict[str, Any]:
    """Commande générée depuis le catalogue 'tool' (cmd_json)."""
    d._conn.execute(
        "INSERT INTO tool_commands (tool_ref, command_name, cmd_json, "
        "privilege_tag, created_at, updated_at) VALUES (?,?,?,?, "
        "datetime('now'), datetime('now')) "
        "ON CONFLICT(tool_ref, command_name) DO UPDATE SET "
        "cmd_json=excluded.cmd_json, privilege_tag=excluded.privilege_tag, "
        "updated_at=datetime('now')",
        (tool_ref, command_name, cmd_json, privilege_tag))
    d._conn.commit()
    return {"ok": True, "tool_ref": tool_ref, "command": command_name}
