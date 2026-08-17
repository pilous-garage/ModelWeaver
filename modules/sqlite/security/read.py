"""security.read — lectures du domaine security.db."""

from __future__ import annotations

from typing import Dict, List, Optional

from .security import get_domain as _d


def get_fs_auth(agent_id: int) -> List[Dict[str, str]]:
    return _d().fs_auth.select(where={"agent_id": agent_id})


def check_fs_auth(agent_id: int, root_path: str) -> Optional[Dict[str, str]]:
    return _d().fs_auth.get({"agent_id": agent_id, "root_path": root_path})


def list_all() -> List[Dict[str, str]]:
    return _d().fs_auth.select(order_by="agent_id")