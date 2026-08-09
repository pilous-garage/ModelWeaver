"""Orchestration multi-agents (appel, messagerie, événements).

Migrée depuis services/skill_manager.py (_exec_*). Les helpers de chemins
(_agent_id_from_home, _inbox_root, _comms_root) sont reproduits à l'identique.
"""

import os
import json
import time
import uuid
from pathlib import Path

from services._common import mw_home


def _agent_id_from_home(home: str, inputs: dict) -> str:
    aid = inputs.get("agent_id", "")
    if aid:
        return str(aid)
    parts = Path(home).parts
    if "agent_home" in parts:
        return str(parts[parts.index("agent_home") + 1])
    return ""


def _inbox_root(agent_id: str) -> Path:
    return mw_home() / "inbox" / str(agent_id)


def _comms_root(chatroom_id: str) -> Path:
    return mw_home() / "comms" / str(chatroom_id)


def call_agent(inputs: dict, home: str) -> dict:
    target = inputs.get("target", "")
    request = inputs.get("request", "")
    provider = inputs.get("provider_ref", "")
    model = inputs.get("model_ref", "")
    if not target:
        return {"ok": False, "error": "target requis"}
    try:
        from modules.sql.db import AgentsDB
        from services.agent_manager.service import Agent, AgentManager
        db = AgentsDB()
        mgr = AgentManager(db=db)
        row = mgr.get_by_name(target)
        if not row:
            return {"ok": False, "error": f"agent '{target}' introuvable"}
        agent = Agent.hydrate(row["agent_id"], db)
        res = agent.execute(request, provider_ref=provider, model_ref=model)
        agent.dehydrate()
        return {"ok": True, "result": res}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_budget(inputs: dict, home: str) -> dict:
    from services.tarif import check_budget
    provider = inputs.get("provider_ref", "")
    model = inputs.get("model_ref", "")
    try:
        return {"budget": check_budget(provider, model)}
    except Exception as e:
        return {"budget": {}, "error": str(e)}


def emit_event(inputs: dict, home: str) -> dict:
    from services.lifecycle import get_event_bus, HookEvent
    event_type = inputs.get("event_type", "custom")
    agent_id = _agent_id_from_home(home, inputs)
    payload = inputs.get("payload", {}) or {}
    # Journal agent-level
    log_path = os.path.join(os.path.abspath(home), "ctx", "events.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": event_type, "payload": payload,
                            "ts": time.time()}) + "\n")
    get_event_bus().publish(HookEvent(
        hook_type=str(event_type), agent_id=agent_id, result=payload))
    return {"ok": True}


def ask_user(inputs: dict, home: str) -> dict:
    agent_id = _agent_id_from_home(home, inputs)
    question = inputs.get("question", "")
    qid = str(uuid.uuid4())
    store = os.path.join(os.path.abspath(home), "ctx", "ask")
    os.makedirs(store, exist_ok=True)
    Path(os.path.join(store, f"{qid}.json")).write_text(
        json.dumps({"question": question, "answered": False,
                    "answer": None, "ts": time.time()},
                   ensure_ascii=False), encoding="utf-8")
    return {"question_id": qid, "answered": False, "agent_id": agent_id}


def ask_authorisation(inputs: dict, home: str) -> dict:
    """Demande une autorisation (path read/write ou commande whitelistée).

    Le skill soumet une AuthorizationRequest (pending_user) via le
    request_handler partagé — l'utilisateur la verra et pourra approuver/
    refuser. L'agent doit continuer sans (continue_anyway=True).

    types d'action :
      - path_read  : autoriser la LECTURE d'un chemin (target {path})
      - path_write : autoriser l'ÉCRITURE sur un chemin (target {path})
      - command    : autoriser une commande shell (target {command})
    """
    agent_id = _agent_id_from_home(home, inputs)
    action = inputs.get("action", "")
    target = {}
    if action in ("path_read", "path_write"):
        p = inputs.get("path", "")
        if not p:
            return {"ok": False, "error": "path requis pour path_read/path_write"}
        target = {"path": p, "mode": "read" if action == "path_read" else "write"}
    elif action == "command":
        c = inputs.get("command", "")
        if not c:
            return {"ok": False, "error": "command requis pour command"}
        target = {"command": c}
    else:
        return {"ok": False,
                "error": "action doit être path_read | path_write | command"}
    reason = inputs.get("reason", "")
    scope = inputs.get("scope", "once")
    from AgentsCatalogue.lib.shell.auth_request import (
        AuthorizationRequest, AuthScope, RequestStatus, RequestType,
        request_handler)
    try:
        scope_enum = AuthScope(scope)
    except ValueError:
        scope_enum = AuthScope.ONCE
    try:

        # ── AUTO-APPROVE du propre espace ──
        # Un agent a TOUJOURS le droit de lire/écrire dans SON home et SON
        # workspace (agent_home/{id}/…). Sans ça, chaque opération sur son
        # propre espace déclenche une demande → backlog (853 pending observés).
        # On auto-approuve via un grant 'auto' (source=auto) pour cette cible.
        _own_ok = False
        try:
            from services._common import mw_home as _mwh
            _own_root = str(_mwh() / "agent_home" / str(agent_id))
            if action in ("path_read", "path_write"):
                p = target.get("path", "")
                if p and (p.startswith(_own_root)
                          or f"/agent_home/{agent_id}" in p):
                    _own_ok = True
            elif action == "command":
                # Commandes sûres sur le propre espace (git, shell local).
                c = target.get("command", "").lower()
                _own_ok = c.startswith(("git ", "cd ", "ls ", "cat ", "mkdir ",
                                        "pwd ", "echo ", "find "))
        except Exception:
            _own_ok = False
        if _own_ok:
            req_auto = AuthorizationRequest(
                agent_id=agent_id, team_id=inputs.get("team_id"),
                action=action, target=target, reason=reason,
                request_type=RequestType.LIVE, scope=AuthScope.DAY,
                approver_level="auto",
                conversation_id=inputs.get("conversation_id"))
            req_auto.status = RequestStatus.APPROVED
            request_handler._add_grant(agent_id, req_auto, True,
                                       approver_id="auto", source="auto")
            request_handler._persist(req_auto)
            return {
                "ok": True, "request_id": req_auto.request_id,
                "action": action, "target": target, "status": "approved",
                "approver_level": "auto", "scope": "day",
                "continue_anyway": True,
                "note": "auto-approuvé : espace propre de l'agent",
            }

        # Par défaut la demande va au LEADER (approver_level='leader').
        # Si l'agent demande directement un humain, escalate d'emblée.
        if str(inputs.get("to", "leader")).lower() == "human":
            req_type = RequestType.PENDING_USER
            level = "human"
        else:
            req_type = RequestType.PENDING_LEADER
            level = "leader"

        req = AuthorizationRequest(
            agent_id=agent_id,
            team_id=inputs.get("team_id"),
            action=action,
            target=target,
            reason=reason,
            request_type=req_type,
            scope=scope_enum,
            approver_level=level,
            conversation_id=inputs.get("conversation_id"),
        )
        submitted = request_handler.submit(req)
        # Tracer dans le home pour l'agent (référence).
        store = os.path.join(os.path.abspath(home), "ctx", "auth")
        os.makedirs(store, exist_ok=True)
        Path(os.path.join(store, f"{submitted.request_id}.json")).write_text(
            json.dumps(submitted.to_dict(), ensure_ascii=False),
            encoding="utf-8")

        # ── Retransmission automatique au LEADER de la team ──
        # Le gestionnaire d'auth achemine la demande au leader (orchestrateur
        # de la team) : message dans son inbox + signal wakeup. Le leader la
        # voit via l'entrypoint is_asked_auth (ou auth_review), la valide par
        # un call LLM, puis décide (auth_decide).
        _notified = False
        try:
            from services.agent_manager.service import AgentManager
            from modules.sql.db import AgentsDB
            _mgr = AgentManager(db=AgentsDB())
            _team = _mgr.get_team(int(agent_id))
            _leader = _mgr.get_leader(_team.get("team_id", -1))
            if _leader:
                _box = _inbox_root(str(_leader.get("agent_id")))
                _box.mkdir(parents=True, exist_ok=True)
                _msg = {
                    "from": agent_id,
                    "type": "auth_request",
                    "request_id": submitted.request_id,
                    "content": (f"Autorisation demandée : {action} sur "
                                f"{json.dumps(target, ensure_ascii=False)[:200]}"
                                f" (raison: {reason[:200]})"),
                    "ts": time.time(),
                }
                (_box / f"{submitted.request_id}.json").write_text(
                    json.dumps(_msg, ensure_ascii=False), encoding="utf-8")
                try:
                    _mgr.send_signal(_leader["agent_id"], "wakeup",
                                     {"kind": "auth", "request_id":
                                      submitted.request_id})
                except Exception:
                    pass
                _notified = True
        except Exception:
            pass

        return {
            "ok": True,
            "request_id": submitted.request_id,
            "action": action,
            "target": target,
            "status": "pending",
            "approver_level": level,
            "scope": scope,
            "continue_anyway": True,
            "leader_notified": _notified,
            "note": ("Demande envoyée au leader de la team — continue sans "
                     "attendre (le leader l'examinera et pourra l'autoriser, "
                     "la refuser ou la transmettre à un humain)."),
        }
    except Exception as e:
        return {"ok": False, "error": f"soumission autorisation: {e}"}


def message_send(inputs: dict, home: str) -> dict:
    to = inputs.get("to_agent_id", "")
    sender = inputs.get("from_agent_id", inputs.get("agent_id", ""))
    content = inputs.get("content", "")
    if not to:
        return {"ok": False, "error": "to_agent_id requis"}
    box = _inbox_root(to)
    box.mkdir(parents=True, exist_ok=True)
    msg_id = str(uuid.uuid4())
    (box / f"{msg_id}.json").write_text(json.dumps({
        "from": sender, "content": content, "ts": time.time(),
    }, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "message_id": msg_id}


def message_recv(inputs: dict, home: str) -> dict:
    agent_id = inputs.get("agent_id", "")
    if not agent_id:
        return {"messages": [], "error": "agent_id requis"}
    box = _inbox_root(agent_id)
    if not box.exists():
        return {"messages": []}
    msgs = []
    for f in sorted(box.glob("*.json")):
        try:
            msgs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    if inputs.get("clear", False):
        for f in box.glob("*.json"):
            f.unlink()
    return {"messages": msgs, "count": len(msgs)}


def chatroom_post(inputs: dict, home: str) -> dict:
    cid = inputs.get("chatroom_id", "")
    agent = inputs.get("agent_id", "")
    content = inputs.get("content", "")
    if not cid:
        return {"ok": False, "error": "chatroom_id requis"}
    root = _comms_root(cid)
    root.mkdir(parents=True, exist_ok=True)
    log = root / "chatroom.jsonl"
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"agent": agent, "content": content,
                            "ts": time.time()}, ensure_ascii=False) + "\n")
    return {"ok": True}


def chatroom_read(inputs: dict, home: str) -> dict:
    cid = inputs.get("chatroom_id", "")
    last_n = int(inputs.get("last_n", 50))
    if not cid:
        return {"messages": [], "error": "chatroom_id requis"}
    log = _comms_root(cid) / "chatroom.jsonl"
    if not log.exists():
        return {"messages": []}
    lines = log.read_text(encoding="utf-8").splitlines()
    msgs = []
    for ln in lines[-last_n:]:
        try:
            msgs.append(json.loads(ln))
        except Exception:
            pass
    return {"messages": msgs, "count": len(msgs)}


def auth_review(inputs: dict, home: str) -> dict:
    """Le leader liste les demandes d'autorisation en attente de SA team.

    Vérifie le rôle leader (ou humain pour voir les escalades).
    """
    agent_id = _agent_id_from_home(home, inputs)
    from AgentsCatalogue.lib.shell.auth_request import request_handler
    team_id = inputs.get("team_id", "")
    level = inputs.get("level", "leader")  # leader | human
    if level == "human":
        pend = request_handler.get_pending_user_authorizations()
    else:
        pend = request_handler.get_pending_leader_authorizations(team_id or None)
    return {
        "ok": True,
        "level": level,
        "team_id": team_id,
        "count": len(pend),
        "requests": [r.to_dict() for r in pend],
    }


def auth_decide(inputs: dict, home: str) -> dict:
    """Le leader/humain décide sur une demande d'autorisation.

    decision : allow | deny | escalate | ask_reason
    scope    : once | run | day | forever (pour allow/deny)
    """
    from AgentsCatalogue.lib.shell.auth_request import (
        AuthScope, request_handler)
    request_id = inputs.get("request_id", "")
    decision = inputs.get("decision", "")
    scope = inputs.get("scope", "once")
    reason = inputs.get("reason", "")
    approver = inputs.get("approver_id") or _agent_id_from_home(home, inputs)
    try:
        scope_enum = AuthScope(scope)
    except ValueError:
        scope_enum = AuthScope.ONCE
    if not request_id:
        return {"ok": False, "error": "request_id requis"}
    return request_handler.decide(
        request_id, str(approver), decision, scope=scope_enum, reason=reason)


def is_asked_auth(inputs: dict, home: str) -> dict:
    """Entrypoint du LEADER : liste les demandes d'autorisation reçues.

    Le gestionnaire d'auth retransmet chaque demande d'un membre dans l'inbox
    du leader (type=auth_request). Ce skill lit l'inbox du leader et présente
    les demandes en attente (avec leur contenu depuis request_handler si
    encore en mémoire, sinon le message de l'inbox). Le leader décide ensuite
    via team/auth_decide@v1.
    """
    agent_id = _agent_id_from_home(home, inputs)
    include_resolved = bool(inputs.get("include_resolved", False))
    requests = []
    box = _inbox_root(str(agent_id))
    if box.is_dir():
        from AgentsCatalogue.lib.shell.auth_request import request_handler
        for f in sorted(box.glob("*.json")):
            try:
                msg = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if msg.get("type") != "auth_request":
                continue
            req_id = msg.get("request_id", "")
            detail = None
            if req_id:
                try:
                    detail = request_handler.get(req_id)
                except Exception:
                    detail = None
            if not include_resolved and detail is not None \
                    and detail.is_resolved:
                continue
            requests.append({
                "request_id": req_id,
                "from": msg.get("from"),
                "content": msg.get("content", ""),
                "ts": msg.get("ts"),
                "detail": detail.to_dict() if detail else None,
            })
    return {"ok": True, "agent_id": agent_id, "count": len(requests),
            "requests": requests}


__skills__ = [
    "call_agent", "ask_user", "ask_authorisation", "emit_event", "get_budget",
    "message_send", "message_recv", "chatroom_post", "chatroom_read",
    "auth_review", "auth_decide", "is_asked_auth",
]
