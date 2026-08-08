"""team_observability — skills de visibilité de la team pour les agents.

Permet à un agent (ex. le pilote dev-chat) d'observer l'activité de sa team :
qui sont les membres, qui tourne, leurs logs FSM récents, et les process
actifs du système. Accès direct aux bases (AgentsDB) et au filesystem des
homes — pas de round-trip HTTP.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._common import mw_home  # noqa: E402


def _agent_db():
    from modules.sql.db import AgentsDB
    return AgentsDB()


def team_members(inputs: dict, home: str) -> dict:
    """Liste les membres d'une team (par nom exact, ex. 'team:dev-chat')."""
    team = inputs.get("team", "")
    if not team:
        return {"ok": False, "error": "team requis (ex. team:dev-chat)"}
    db = _agent_db()
    try:
        rows = db.conn.execute(
            "SELECT agent_id, name, ref, role_type, occupation, status, "
            "       last_active_at FROM agents "
            "WHERE name LIKE ? ORDER BY agent_id",
            (team + "/%",)).fetchall()
        members = [dict(r) for r in rows]
        for m in members:
            rt = db.conn.execute(
                "SELECT heartbeat_at, current_step FROM agent_runtime "
                "WHERE agent_id = ?", (m["agent_id"],)).fetchone()
            m["running"] = bool(rt)
            m["heartbeat"] = rt["heartbeat_at"] if rt else None
            m["current_step"] = rt["current_step"] if rt else None
        return {"ok": True, "team": team, "members": members,
                "count": len(members)}
    finally:
        db.close()


def agent_status(inputs: dict, home: str) -> dict:
    """Statut d'un agent : occupation, running, step courant, dernière activité."""
    agent_id = inputs.get("agent_id")
    name = inputs.get("name", "")
    if not agent_id and not name:
        return {"ok": False, "error": "agent_id ou name requis"}
    db = _agent_db()
    try:
        if agent_id:
            row = db.conn.execute(
                "SELECT agent_id, name, role_type, occupation, status, "
                "       created_at, last_active_at FROM agents "
                "WHERE agent_id = ?", (agent_id,)).fetchone()
        else:
            row = db.conn.execute(
                "SELECT agent_id, name, role_type, occupation, status, "
                "       created_at, last_active_at FROM agents "
                "WHERE name = ?", (name,)).fetchone()
        if not row:
            return {"ok": False, "error": "agent introuvable"}
        a = dict(row)
        rt = db.conn.execute(
            "SELECT heartbeat_at, current_step FROM agent_runtime "
            "WHERE agent_id = ?", (a["agent_id"],)).fetchone()
        a["running"] = bool(rt)
        a["heartbeat"] = rt["heartbeat_at"] if rt else None
        a["current_step"] = rt["current_step"] if rt else None
        return {"ok": True, "agent": a}
    finally:
        db.close()


def agent_log(inputs: dict, home: str) -> dict:
    """Dernières lignes du log FSM d'un agent (agent_home/{id}/log/fsm_*.log)."""
    agent_id = inputs.get("agent_id", "")
    n = min(int(inputs.get("lines", 40)), 200)
    if not agent_id:
        return {"ok": False, "error": "agent_id requis"}
    agent_home = mw_home() / "agent_home" / str(agent_id) / "log"
    if not agent_home.is_dir():
        return {"ok": False, "error": f"pas de log pour l'agent {agent_id}"}
    logs = sorted(agent_home.glob("fsm_*.log"))
    if not logs:
        return {"ok": False, "error": f"aucun log fsm_*.log pour {agent_id}"}
    lines = []
    for f in logs[-3:]:  # les 3 plus récents
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            lines.append(f"# {f.name}")
            lines.extend(content.strip().splitlines()[-n:])
        except Exception:
            pass
    return {"ok": True, "agent_id": agent_id, "log": "\n".join(lines)}


def process_list(inputs: dict, home: str) -> dict:
    """Liste les process supervisés du système (superviseur, daemon, services…)."""
    import json
    try:
        sockets = json.loads(
            (mw_home() / "run" / "sockets.json").read_text(encoding="utf-8"))
        services = {k: v for k, v in sockets.items() if isinstance(v, dict)
                    and "socket" in v}
    except Exception:
        services = {}
    out = []
    for name, info in services.items():
        pid = info.get("pid")
        out.append({
            "name": name,
            "pid": pid,
            "socket": info.get("socket", ""),
            "alive": bool(pid) and Path(f"/proc/{pid}").exists(),
        })
    return {"ok": True, "processes": out, "count": len(out)}


__skills__ = ["team_members", "agent_status", "agent_log", "process_list"]
