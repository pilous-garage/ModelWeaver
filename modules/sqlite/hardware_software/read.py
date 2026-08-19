"""hardware_software.read — lectures hardware_software.db."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db


def get_software(d: Db, tool_ref: str) -> Optional[Dict[str, Any]]:
    r = d._conn.execute(
        "SELECT * FROM installed_software WHERE tool_ref = ?", (tool_ref,)).fetchone()
    return dict(r) if r else None


def list_software(d: Db, status: str = "") -> List[Dict[str, Any]]:
    q = "SELECT * FROM installed_software"
    p: list = []
    if status:
        q += " WHERE status = ?"
        p.append(status)
    q += " ORDER BY name"
    return [dict(r) for r in d._conn.execute(q, p).fetchall()]


def effective_privilege(row: Dict[str, Any]) -> str:
    """Privilège effectif : override user > tag par défaut."""
    return row.get("privilege_override") or row.get("privilege_tag") or "default"


def list_commands(d: Db, tool_ref: str = "") -> List[Dict[str, Any]]:
    q = "SELECT * FROM tool_commands"
    p: list = []
    if tool_ref:
        q += " WHERE tool_ref = ?"
        p.append(tool_ref)
    q += " ORDER BY command_name"
    return [dict(r) for r in d._conn.execute(q, p).fetchall()]
