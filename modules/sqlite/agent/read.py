"""agent.read — lectures du domaine agents.db.

Toutes les fonctions sont des enveloppes minces sur base.Table (aucun SQL
direct ici). Lectures via db_ro() (jamais d'écriture en lecture).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db
from modules.sqlite.agent import db_ro


def _tables() -> Db:
    return db_ro()


def get_agent(agent_id: int, cols: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    return _tables().table("agents").get({"agent_id": agent_id}, cols=cols)


def get_agent_by_name(name: str, cols: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    return _tables().table("agents").get({"name": name}, cols=cols)


def list_agents(status: Optional[str] = None, role_type: Optional[str] = None) -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {}
    if status:
        where["status"] = status
    if role_type:
        where["role_type"] = role_type
    return _tables().table("agents").select(where=where or None)


def get_runtime(agent_id: int) -> Optional[Dict[str, Any]]:
    return _tables().table("agent_runtime").get({"agent_id": agent_id})


def list_runtime() -> List[Dict[str, Any]]:
    return _tables().table("agent_runtime").select()


def get_metrics(agent_id: int) -> Optional[Dict[str, Any]]:
    return _tables().table("agent_metrics").get({"agent_id": agent_id})


def list_signals(agent_id: Optional[int] = None, status: Optional[str] = None,
                 order_by: str = "signal_id") -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {}
    if agent_id is not None:
        where["agent_id"] = agent_id
    if status:
        where["status"] = status
    return _tables().table("agent_signals").select(where=where or None, order_by=order_by)


def get_signal(signal_id: int) -> Optional[Dict[str, Any]]:
    return _tables().table("agent_signals").get({"signal_id": signal_id})


def list_entrypoints(agent_id: Optional[int] = None, order_by: str = "priority") -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {"agent_id": agent_id} if agent_id is not None else {}
    return _tables().table("agent_entrypoints").select(where=where or None, order_by=order_by)


def read_meta(key: str, default: int = 0) -> int:
    r = _tables().table("meta").get({"key": key})
    return int(r["value"]) if r else default


def get_auth_request(request_id: str) -> Optional[Dict[str, Any]]:
    return _tables().table("auth_requests").get({"request_id": request_id})


def list_auth_requests(status: Optional[str] = None, approver_level: Optional[str] = None,
                       agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    where: Dict[str, Any] = {}
    if status:
        where["status"] = status
    if approver_level:
        where["approver_level"] = approver_level
    if agent_id:
        where["agent_id"] = agent_id
    return _tables().table("auth_requests").select(where=where or None)