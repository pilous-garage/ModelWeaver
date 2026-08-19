"""read — lectures thin du domaine buffer.

CRUD pur sur buffer_op ; les écritures (dépôt, marquage, purge) vivent dans
write.py ; le consumer (qui applique les ops dans le domaine local) vit dans
local/write.py — voir la règle dans base.py."""

from __future__ import annotations

from typing import Any, Dict, List

from modules.sqlite.base import Db


def status(db: Db, external_tag: str = "", status: str = "") -> List[Dict[str, Any]]:
    """Ops du buffer, filtrées par (external_tag, status), DESC (limit 500).
    Les ops `applied` sont conservées (audit) ; les `error` sont rejouables."""
    w: Dict[str, Any] = {}
    if external_tag:
        w["external_tag"] = external_tag
    if status:
        w["status"] = status
    return db.table("buffer_op").select(
        where=w or None, order_by="op_id DESC", limit=500)