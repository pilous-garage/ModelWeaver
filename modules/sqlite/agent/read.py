"""agent.read — lectures du domaine agents.db.

Toutes les fonctions sont des enveloppes minces sur base.Table (aucun SQL
direct ici). Le domaine est ouvert (pas de token) : les lectures passent
directement.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .agent import get_domain as _d


def get_agent(agent_id: int, cols: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    return _d().agents.get({"agent_id": agent_id}, cols=cols)


def get_agent_by_name(name: str, cols: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    return _d().agents.get({"name": name}, cols=cols)


def list_agents(status: Optional[str] = None, role_type: Optional[str] = None) -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {}
    if status:
        where["status"] = status
    if role_type:
        where["role_type"] = role_type
    return _d().agents.select(where=where or None)


def get_runtime(agent_id: int) -> Optional[Dict[str, Any]]:
    return _d().runtime.get({"agent_id": agent_id})


def list_runtime() -> List[Dict[str, Any]]:
    return _d().runtime.select()


def get_metrics(agent_id: int) -> Optional[Dict[str, Any]]:
    return _d().metrics.get({"agent_id": agent_id})


def list_signals(agent_id: Optional[int] = None, status: Optional[str] = None,
                 order_by: str = "signal_id") -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {}
    if agent_id is not None:
        where["agent_id"] = agent_id
    if status:
        where["status"] = status
    return _d().signals.select(where=where or None, order_by=order_by)


def get_signal(signal_id: int) -> Optional[Dict[str, Any]]:
    return _d().signals.get({"signal_id": signal_id})


def list_entrypoints(agent_id: Optional[int] = None, order_by: str = "priority") -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {"agent_id": agent_id} if agent_id is not None else {}
    return _d().entrypoints.select(where=where or None, order_by=order_by)


def read_meta(key: str, default: int = 0) -> int:
    r = _d().meta.get({"key": key})
    return int(r["value"]) if r else default


def get_auth_request(request_id: str) -> Optional[Dict[str, Any]]:
    return _d().auth_requests.get({"request_id": request_id})


def list_auth_requests(status: Optional[str] = None, approver_level: Optional[str] = None,
                       agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {}
    if status:
        where["status"] = status
    if approver_level:
        where["approver_level"] = approver_level
    if agent_id:
        where["agent_id"] = agent_id
    return _d().auth_requests.select(where=where or None)