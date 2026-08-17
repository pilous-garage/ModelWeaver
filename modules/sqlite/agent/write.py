"""agent.write — écritures du domaine agents.db.

Enveloppes minces sur base.Table (aucun SQL direct). Le domaine est ouvert
(pas de token). Les opérations atomiques multi-étapes utilisent
`_d().db.in_write()` (transaction BEGIN/COMMIT).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from .agent import get_domain as _d


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def create_agent(ref: str, name: str, role_type: str,
                 occupation: str = "noncontinue", status: str = "INIT",
                 config_json: Optional[str] = None,
                 resources_json: Optional[str] = None,
                 home: str = "",
                 id_proprietaire: Optional[str] = None,
                 id_team: Optional[int] = None,
                 token: str = "") -> int:
    data: Dict[str, Any] = {
        "ref": ref,
        "name": name,
        "role_type": role_type,
        "occupation": occupation,
        "status": status,
        "config_json": config_json or "{}",
        "resources_json": resources_json or "{}",
        "home": home,
        "id_proprietaire": id_proprietaire,
        "id_team": id_team,
    }
    aid = _d().agents.add(data, token=token)
    # entrypoints par défaut (main / cancel / pause)
    with _d().db.in_write():
        _d().entrypoints.add([
            {"agent_id": aid, "ep_name": "main", "priority": 0},
            {"agent_id": aid, "ep_name": "cancel", "priority": 2,
             "trigger": "signal:cancel"},
            {"agent_id": aid, "ep_name": "pause", "priority": 3,
             "trigger": "signal:pause"},
        ], token=token)
    return aid


def update_status(agent_id: int, status: str, token: str = "") -> None:
    _d().agents.update({"agent_id": agent_id}, {"status": status}, token=token)


def save_state(agent_id: int, state_json: str, token: str = "") -> None:
    _d().agents.update({"agent_id": agent_id}, {"state_json": state_json},
                         token=token)


def set_successor(agent_id: int, successor_id: Optional[int], token: str = "") -> None:
    _d().agents.update({"agent_id": agent_id},
                         {"successor_id": successor_id}, token=token)


def terminate(agent_id: int, successor_id: Optional[int] = None,
              token: str = "") -> None:
    with _d().db.in_write():
        _d().agents.update({"agent_id": agent_id},
                             {"status": "TERMINATED", "successor_id": successor_id},
                             token=token)
        _d().runtime.update({"agent_id": agent_id},
                              {"heartbeat_at": None, "thread_id": None},
                              token=token)


def delete_agent(agent_id: int, token: str = "") -> int:
    return _d().agents.remove({"agent_id": agent_id}, token=token)


def heartbeat(agent_id: int, thread_id: Optional[str] = None, pid: Optional[int] = None,
              current_step: Optional[str] = None,
              id_proprietaire: Optional[str] = None, id_team: Optional[int] = None,
              token: str = "") -> None:
    data: Dict[str, Any] = {
        "agent_id": agent_id,
        "heartbeat_at": _now(),
        "thread_id": thread_id,
        "pid": pid,
        "current_step": current_step,
        "id_proprietaire": id_proprietaire,
        "id_team": id_team,
    }
    _d().runtime.upsert(data, conflict_cols=["agent_id"], token=token)


def record_metrics(agent_id: int, token: str = "", **fields: Any) -> None:
    _d().metrics.upsert({"agent_id": agent_id, **fields},
                          conflict_cols=["agent_id"], token=token)


def create_signal(agent_id: int, signal_type: str,
                  payload_json: Optional[str] = None,
                  token: str = "") -> int:
    return _d().signals.add(
        {"agent_id": agent_id, "type": signal_type,
         "payload_json": payload_json},
        token=token,
    )


def ack_signal(signal_id: int, token: str = "") -> None:
    _d().signals.update({"signal_id": signal_id},
                          {"status": "ACKED", "acknowledged_at": _now()},
                          token=token)


def complete_signal(signal_id: int, token: str = "") -> None:
    _d().signals.update({"signal_id": signal_id},
                          {"status": "COMPLETED", "completed_at": _now()},
                          token=token)


def upsert_entrypoint(agent_id: int, ep_name: str, priority: int = 0,
                      enabled: int = 1, trigger: str = "", step_id: str = "",
                      token: str = "") -> None:
    _d().entrypoints.upsert(
        {"agent_id": agent_id, "ep_name": ep_name, "priority": priority,
         "enabled": enabled, "trigger": trigger, "step_id": step_id},
        conflict_cols=["agent_id", "ep_name"],
        token=token,
    )


def set_meta(key: str, value: int, token: str = "") -> None:
    _d().meta.upsert({"key": key, "value": value},
                       conflict_cols=["key"], token=token)


def create_auth_request(request_id: str, agent_id: str, action: str,
                        target: Optional[str] = None, reason: str = "",
                        team_id: Optional[str] = None,
                        scope: str = "once", approver_level: str = "leader",
                        request_type: str = "pending_leader",
                        status: str = "pending", created_at: Optional[float] = None,
                        token: str = "") -> int:
    return _d().auth_requests.add({
        "request_id": request_id,
        "agent_id": agent_id,
        "action": action,
        "target": target,
        "reason": reason,
        "team_id": team_id,
        "scope": scope,
        "approver_level": approver_level,
        "request_type": request_type,
        "status": status,
        "created_at": created_at,
    }, token=token)


def update_auth_request(request_id: str, token: str = "", **fields: Any) -> int:
    return _d().auth_requests.update({"request_id": request_id}, fields, token=token)