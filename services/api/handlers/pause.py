"""Handlers API pour le mécanisme de pause partagé (projet / team / agent)."""

from __future__ import annotations

from typing import Any, Dict

from services.api.router import register


def _manager():
    from services.agent_manager.service import AgentManager
    from modules.sql.db import AgentsDB
    return AgentManager(db=AgentsDB())


def op_pause_set(params: Dict[str, Any]) -> Dict[str, Any]:
    """Active la pause pour un niveau donné.

    Body attendu :
      {"level": "project"|"team"|"agent", "ref": "<identifiant>"}
    """
    level = str((params or {}).get("level", "")).lower().strip()
    ref = str((params or {}).get("ref", "")).strip()
    if level not in {"project", "team", "agent"} or not ref:
        return {"status": "error", "error": "level/ref requis"}
    from AgentFrameWork.pause_flag_store import set_paused
    set_paused(level, ref, True)
    return {"status": "ok", "paused": True, "level": level, "ref": ref}


def op_pause_clear(params: Dict[str, Any]) -> Dict[str, Any]:
    """Désactive la pause pour un niveau donné."""
    level = str((params or {}).get("level", "")).lower().strip()
    ref = str((params or {}).get("ref", "")).strip()
    if level not in {"project", "team", "agent"} or not ref:
        return {"status": "error", "error": "level/ref requis"}
    from AgentFrameWork.pause_flag_store import set_paused
    set_paused(level, ref, False)
    return {"status": "ok", "paused": False, "level": level, "ref": ref}


def op_pause_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Retourne l'état de pause pour un niveau/ref donné."""
    level = str((params or {}).get("level", "")).lower().strip()
    ref = str((params or {}).get("ref", "")).strip()
    if level not in {"project", "team", "agent"} or not ref:
        return {"status": "error", "error": "level/ref requis"}
    from AgentFrameWork.pause_flag_store import get_paused
    return {"status": "ok", "level": level, "ref": ref,
            "paused": bool(get_paused(level, ref))}


def op_pause_wait(params: Dict[str, Any]) -> Dict[str, Any]:
    """Bloque tant que la pause est active pour le niveau/ref donné.

    Utile pour des appels synchrones qui veulent attendre la reprise.
    """
    level = str((params or {}).get("level", "")).lower().strip()
    ref = str((params or {}).get("ref", "")).strip()
    if level not in {"project", "team", "agent"} or not ref:
        return {"status": "error", "error": "level/ref requis"}
    from AgentFrameWork.pause_flag_store import wait_while_paused
    wait_while_paused(**{f"{level}_id" if level == "agent" else level + "_name" if level == "team" else level + "_id": ref})
    return {"status": "ok", "paused": False}


# ── Helpers projet/team/agent dédiés ─────────────────────────────────────

def _set_project_pause(paused: bool) -> Dict[str, Any]:
    from AgentFrameWork.pause_flag_store import set_paused
    set_paused("project", "default", paused)
    return {"status": "ok", "paused": paused, "level": "project", "ref": "default"}


def _set_team_pause(team_name: str, paused: bool) -> Dict[str, Any]:
    from AgentFrameWork.pause_flag_store import set_paused
    team_name = (team_name or "").strip()
    if not team_name:
        return {"status": "error", "error": "team_name requis"}
    set_paused("team", team_name, paused)
    return {"status": "ok", "paused": paused, "level": "team", "ref": team_name}


def _set_agent_pause(agent_id, paused: bool) -> Dict[str, Any]:
    from AgentFrameWork.pause_flag_store import set_paused
    try:
        agent_id = int(agent_id)
    except Exception:
        return {"status": "error", "error": "agent_id invalide"}
    set_paused("agent", str(agent_id), paused)
    return {"status": "ok", "paused": paused, "level": "agent", "ref": str(agent_id)}


def op_project_pause(params):
    return _set_project_pause(True)


def op_project_resume(params):
    return _set_project_pause(False)


def op_project_status(params):
    from AgentFrameWork.pause_flag_store import get_paused
    return {"status": "ok", "level": "project", "ref": "default",
            "paused": bool(get_paused("project", "default"))}


def op_team_pause(params):
    return _set_team_pause(params.get("team_name") or params.get("name", ""), True)


def op_team_resume(params):
    return _set_team_pause(params.get("team_name") or params.get("name", ""), False)


def op_agent_pause(params):
    return _set_agent_pause(params.get("agent_id"), True)


def op_agent_resume(params):
    return _set_agent_pause(params.get("agent_id"), False)


# Enregistrement des routes
register("pause/set", op_pause_set)
register("pause/clear", op_pause_clear)
register("pause/status", op_pause_status)
register("pause/wait", op_pause_wait)

register("project/pause", op_project_pause)
register("project/resume", op_project_resume)
register("project/status", op_project_status)
register("team/pause", op_team_pause)
register("team/resume", op_team_resume)
register("agent/pause", op_agent_pause)
register("agent/resume", op_agent_resume)
