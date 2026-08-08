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
    try:
        from AgentsCatalogue.lib.shell.auth_request import (
            AuthorizationRequest, RequestType, request_handler)
        req = AuthorizationRequest(
            agent_id=agent_id,
            team_id=inputs.get("team_id"),
            action=action,
            target=target,
            reason=reason,
            request_type=RequestType.PENDING_USER,
        )
        submitted = request_handler.submit(req)
        # Tracer dans le home pour l'agent (référence).
        store = os.path.join(os.path.abspath(home), "ctx", "auth")
        os.makedirs(store, exist_ok=True)
        Path(os.path.join(store, f"{submitted.request_id}.json")).write_text(
            json.dumps(submitted.to_dict(), ensure_ascii=False),
            encoding="utf-8")
        return {
            "ok": True,
            "request_id": submitted.request_id,
            "action": action,
            "target": target,
            "status": "pending",
            "continue_anyway": True,
            "note": "Demande envoyée à l'utilisateur — continue sans attendre.",
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


__skills__ = [
    "call_agent", "ask_user", "ask_authorisation", "emit_event", "get_budget",
    "message_send", "message_recv", "chatroom_post", "chatroom_read",
]
