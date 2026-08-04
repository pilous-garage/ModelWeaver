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


# Enregistrement des routes
register("pause/set", op_pause_set)
register("pause/clear", op_pause_clear)
register("pause/status", op_pause_status)
register("pause/wait", op_pause_wait)
