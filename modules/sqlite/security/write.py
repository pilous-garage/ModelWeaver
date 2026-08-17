""" security.write — écritures du domaine security.db."""

from __future__ import annotations

from typing import Dict

from .security import get_domain as _d


def grant_fs_auth(agent_id: int, root_path: str, mode: str, token: str = "") -> None:
    """Accorde un droit FS (r ou rw) sur `root_path` à `agent_id`."""
    mode_norm = "rw" if mode == "rw" else "r"
    _d().fs_auth.upsert(
        {"agent_id": agent_id, "root_path": root_path, "mode": mode_norm},
        conflict_cols=["agent_id", "root_path"],
        token=token,
    )


def revoke_fs_auth(agent_id: int, root_path: str, token: str = "") -> int:
    """Révoque un droit FS. Retourne le nb de lignes supprimées."""
    return _d().fs_auth.remove(
        {"agent_id": agent_id, "root_path": root_path}, token=token
    )


def revoke_all(agent_id: int, token: str = "") -> int:
    """Révoque tous les droits FS d'un agent."""
    return _d().fs_auth.remove({"agent_id": agent_id}, token=token)