"""Orchestration multi-agents (appel, messagerie, événements).

Migrée depuis services/skill_manager.py (_exec_*). Les helpers de chemins
(_agent_id_from_ws, _inbox_root, _comms_root) sont reproduits à l'identique.
"""

import os
import json
import time
import uuid
from pathlib import Path

from services._common import mw_home


def _agent_id_from_ws(ws: str, inputs: dict) -> str:
    aid = inputs.get("agent_id", "")
    if aid:
        return str(aid)
    parts = Path(ws).parts
    if "memagent" in parts:
        return str(parts[parts.index("memagent") + 1])
    return ""


def _inbox_root(agent_id: str) -> Path:
    return mw_home() / "inbox" / str(agent_id)


def _comms_root(chatroom_id: str) -> Path:
    return mw_home() / "comms" / str(chatroom_id)


def call_agent(inputs: dict, ws: str) -> dict:
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


def get_budget(inputs: dict, ws: str) -> dict:
    from services.tarif import check_budget
    provider = inputs.get("provider_ref", "")
    model = inputs.get("model_ref", "")
    try:
        return {"budget": check_budget(provider, model)}
    except Exception as e:
        return {"budget": {}, "error": str(e)}


def emit_event(inputs: dict, ws: str) -> dict:
    from services.lifecycle import get_event_bus, HookEvent
    event_type = inputs.get("event_type", "custom")
    agent_id = _agent_id_from_ws(ws, inputs)
    payload = inputs.get("payload", {}) or {}
    # Journal agent-level
    log_path = os.path.join(os.path.abspath(ws), "ctx", "events.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": event_type, "payload": payload,
                            "ts": time.time()}) + "\n")
    get_event_bus().publish(HookEvent(
        hook_type=str(event_type), agent_id=agent_id, result=payload))
    return {"ok": True}


def ask_user(inputs: dict, ws: str) -> dict:
    agent_id = _agent_id_from_ws(ws, inputs)
    question = inputs.get("question", "")
    qid = str(uuid.uuid4())
    store = os.path.join(os.path.abspath(ws), "ctx", "ask")
    os.makedirs(store, exist_ok=True)
    Path(os.path.join(store, f"{qid}.json")).write_text(
        json.dumps({"question": question, "answered": False,
                    "answer": None, "ts": time.time()},
                   ensure_ascii=False), encoding="utf-8")
    return {"question_id": qid, "answered": False, "agent_id": agent_id}


def message_send(inputs: dict, ws: str) -> dict:
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


def message_recv(inputs: dict, ws: str) -> dict:
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


def chatroom_post(inputs: dict, ws: str) -> dict:
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


def chatroom_read(inputs: dict, ws: str) -> dict:
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
    "call_agent", "ask_user", "emit_event", "get_budget",
    "message_send", "message_recv", "chatroom_post", "chatroom_read",
]
