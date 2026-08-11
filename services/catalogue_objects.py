#!/usr/bin/env python3
"""catalogue_objects — Objets racines runtime du PathEvaluator.

Objets globaux accessibles depuis le langage YAML :
    catalogue.*  (déjà existant — skills du catalogue, résolus à runtime)
    team.*       → la team du membre courant (via la BDD agents)
    daemon.*     → le daemon RESTREINT (données système, borné par privilèges)

Chaque objet est un RuntimeObject dont les méthodes sont déclarées dans le
MethodRegistry global (input/output typés, polymorphes).

team :
    team.members                        → list  (membres de la team)
    team.members.reduce_pattern(name=*) → list  (filtre par pattern)
    team.members.first                  → singleton_or_none
    team.members.first.get_home()       → singleton_or_none (home du membre)
    team.chatroom.read()                → singleton_or_none (dernier message)

daemon (restreint — chaque appel est borné par privileges) :
    daemon.info()                       → singleton (version, uptime)
    daemon.agents()                     → list (agents actifs, lus via BDD)
"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.agent_graph_utils.resolution import (
    GLOBAL_REGISTRY, RuntimeObject, PathEvaluator,
    LIST, LIST_NOT_EMPTY, SINGLETON, SINGLETON_OR_NONE, NONE_RETURN,
    make_root,
)


# ── helpers ──────────────────────────────────────────────────────────────

def _mw_home() -> Path:
    ev = os.environ.get("MODELWEAVER_HOME") or os.environ.get("MW_HOME")
    if ev:
        return Path(ev)
    p = Path("/opt/modelweaver")
    return p if p.is_dir() else Path.home() / ".modelweaver"


def _get_agent_db():
    from modules.sql.agents_repo import AgentsDB
    return AgentsDB()


def _get_team_of(agent_id) -> Optional[dict]:
    """La team d'un agent (nom + team_id stable) via l'AgentManager."""
    try:
        from services.agent_manager.service import AgentManager
        mgr = AgentManager()
        return mgr.get_team(int(agent_id))
    except Exception:
        return None


# ── implémentations team ─────────────────────────────────────────────────

def _team_members(items, ctx=None, **kw):
    """Impl list→list : les membres de la team (déjà dans la liste)."""
    return items


def _team_members_single(item, ctx=None, **kw):
    """Impl singleton→singleton : si récepteur singleton, membre unique."""
    return item


def _reduce_pattern(items, name="*", role="*", ctx=None, **kw):
    """Impl list→list : filtre les membres par pattern fnmatch sur name/role."""
    out = []
    for m in items:
        mname = (m.get("name") or "") if isinstance(m, dict) else str(m)
        mrole = (m.get("role_type") or "") if isinstance(m, dict) else ""
        if fnmatch.fnmatch(mname, str(name)) and fnmatch.fnmatch(mrole, str(role)):
            out.append(m)
    return out


def _get_home(item, ctx=None, **kw):
    """Impl singleton→singleton_or_none : home de l'agent (agent_home/{id})."""
    if item is None:
        return NONE_RETURN
    aid = item.get("agent_id") if isinstance(item, dict) else item
    if aid is None:
        return NONE_RETURN
    return str(_mw_home() / "agent_home" / str(aid))


def _chatroom_read(item, ctx=None, **kw):
    """Impl singleton→singleton_or_none : dernier message du chatroom."""
    return "<dernier_message_chat>"


def _chatroom_sent(item, ctx=None, **kw):
    return "<messages_envoyes_chat>"


def _chatroom_send(item, msg, ctx=None, **kw):
    """team.chatroom.send(msg) — envoie un message à la chatroom de la team.

    agent_id vient du CONTEXTE (l'agent courant qui appelle) ; workspace_id
    depuis l'objet (si le dict team le porte) sinon résolu par l'agent. Seul
    `msg` est un argument explicite."""
    team = item.get("team") if isinstance(item, dict) else None
    workspace_id = team.get("workspace_id") if isinstance(team, dict) else ""
    agent_id = (ctx or {}).get("agent_id")
    try:
        from modules.sql.workspace import chatroom_send_message
        return chatroom_send_message(agent_id=str(agent_id), msg=str(msg),
                                     workspace_id=workspace_id or "")
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def _register_team_methods() -> None:
    """Registre les méthodes de l'objet `team` (polymorphes)."""
    GLOBAL_REGISTRY.register("team.members", "reduce_pattern",
                             LIST, LIST, _reduce_pattern)
    # get_home : singleton → singleton_or_none
    GLOBAL_REGISTRY.register("team.members", "get_home",
                             SINGLETON, SINGLETON_OR_NONE, _get_home)
    GLOBAL_REGISTRY.register("team.members", "first", LIST,
                             SINGLETON_OR_NONE, lambda items, ctx=None, **kw:
                             (items[0] if items else NONE_RETURN))
    GLOBAL_REGISTRY.register("team.chatroom", "read",
                             SINGLETON, SINGLETON_OR_NONE, _chatroom_read)
    GLOBAL_REGISTRY.register("team.chatroom", "sent",
                             SINGLETON, SINGLETON_OR_NONE, _chatroom_sent)
    GLOBAL_REGISTRY.register("team.chatroom", "send",
                             SINGLETON, SINGLETON, _chatroom_send)


def _register_team_navs() -> None:
    """Attributs NAVIGATEURS : team.chatroom, team.members, team.list_member."""

    def _nav_chatroom(receiver):
        return make_root("team.chatroom", {"team": receiver.value},
                         SINGLETON, receiver._ctx)

    def _nav_members(receiver):
        team = receiver.value or {}
        members = team.get("members", []) if isinstance(team, dict) else []
        return RuntimeObject(members, LIST, "team.members",
                             GLOBAL_REGISTRY, receiver._ctx)

    GLOBAL_REGISTRY.register_nav("team", "chatroom", _nav_chatroom)
    GLOBAL_REGISTRY.register_nav("team", "members", _nav_members)
    GLOBAL_REGISTRY.register_nav("team", "list_member", _nav_members)


# ── implémentations daemon (restreint) ───────────────────────────────────

def _daemon_info(item, ctx=None, **kw):
    return {"version": "0.1.0", "status": "ok", "uptime_s": 0}


def _daemon_agents(item, ctx=None, **kw):
    try:
        from services.agent_manager.service import AgentManager
        return AgentManager().list_active()
    except Exception:
        return []


def _register_daemon_methods() -> None:
    GLOBAL_REGISTRY.register("daemon", "info", SINGLETON, SINGLETON, _daemon_info)
    GLOBAL_REGISTRY.register("daemon", "agents", SINGLETON, LIST, _daemon_agents)


# ── racines exposées ─────────────────────────────────────────────────────

def make_team(agent_id: Optional[int] = None) -> RuntimeObject:
    """team : la team du membre courant (singleton). Méthodes sur ses sous-objets.

    Enrichit le dict team avec le workspace_id de l'agent (variables) pour
    que team.chatroom.* résolve le bon workspace."""
    ctx = {"agent_id": agent_id}
    team = _get_team_of(agent_id) if agent_id is not None else None
    team = dict(team or {})
    if agent_id is not None and not team.get("workspace_id"):
        try:
            from modules.sql.agents_repo import AgentsDB
            db = AgentsDB()
            row = db.conn.execute(
                "SELECT variables_json FROM agents WHERE agent_id = ?",
                (int(agent_id),)).fetchone()
            db.close()
            if row:
                team["workspace_id"] = (json.loads(row["variables_json"] or "{}")
                                        .get("workspace_id", ""))
        except Exception:
            pass
    return make_root("team", team, SINGLETON, ctx)


def make_daemon() -> RuntimeObject:
    return make_root("daemon", {}, SINGLETON, {})


# ── enregistrement au chargement ─────────────────────────────────────────

_register_team_methods()
_register_team_navs()
_register_daemon_methods()


def build_resolution_namespace(agent_id: Optional[int] = None) -> Dict[str, Any]:
    """Namespace complet : catalogue (existant) + team + daemon."""
    from services.catalogue_runtime import make_catalogue
    return {
        "catalogue": make_catalogue(agent_id=str(agent_id or "")),
        "team": make_team(agent_id),
        "daemon": make_daemon(),
        "eval_path": lambda path, **kw: PathEvaluator(
            make_root("team", {}, SINGLETON, {"agent_id": agent_id})
        ).evaluate(path, **kw) if path.startswith("team.") else None,
    }
