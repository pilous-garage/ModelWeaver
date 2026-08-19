"""write — CRUD fin (écritures) du domaine task.

Chaque appel exige le token du writer task (write_task) — sauf `import_local`
qui délègue au writer local. La logique (attribution greedy, découpe,
relâche des dépendances) vit dans local.py — voir la règle dans base.py.

NOTE : le consumer IN du buffer (buffer_op → x_data locale) vit dans le
domaine LOCAL (local/write.import_local) ; le domaine task ne touche pas
le buffer."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db
from modules.sqlite.task import local as L


def _tok(db: Db, token: str) -> str:
    """Token explicite OU token lié de l'instance writer (domaine à writer
    dédié) — les appels sans token s'appuient sur l'instance get_writer."""
    return token or db._write_token


def create_entry_request(db: Db, workspace_id: str, prompt_hash: str,
                         title: str = "", token: str = "") -> Dict[str, Any]:
    db.check_write(_tok(db, token))
    rid = db.table("entry_request").add(
        {"workspace_id": workspace_id, "prompt_hash": prompt_hash,
         "title": title}, token=_tok(db, token))
    return {"ok": True, "entry_request_id": rid, "workspace_id": workspace_id}


def create_task(db: Db, workspace_id: str, entry_request_id: int,
                parent_task_id: Optional[int] = None,
                sub_task_type: str = "", title: str = "",
                description: str = "", status: str = "todo",
                token: str = "") -> Dict[str, Any]:
    return L.create_task(db, workspace_id, entry_request_id, parent_task_id,
                         sub_task_type, title, description, status,
                         token=_tok(db, token))


def update_task(db: Db, task_id: int, changes: Dict[str, Any],
                token: str = "") -> int:
    changes.setdefault("updated_at", _now())
    return db.table("tasks").update({"task_id": task_id}, changes,
                                    token=_tok(db, token))


def set_task_status(db: Db, task_id: int, status: str,
                    tag: str = "", token: str = "") -> Dict[str, Any]:
    changes: Dict[str, Any] = {"status": status}
    if tag:
        changes["tag"] = tag
    db.table("tasks").update({"task_id": task_id}, changes, token=_tok(db, token))
    return {"ok": True, "task_id": task_id, "status": status}


def add_dependency(db: Db, child_id: int, parent_id: int,
                   required_state: str = "done", required_tag: str = "",
                   token: str = "") -> Dict[str, Any]:
    return L.add_dependency(db, child_id, parent_id, required_state,
                            required_tag, token=_tok(db, token))


def release_waiting(db: Db, workspace_id: str = "", token: str = "") -> int:
    """Relâche les waiting_deps dont les dépendances sont satisfaites — à
    appeler quand un parent passe à l'état requis (ou sur tick failsafe)."""
    return L.release_satisfied_children(db, workspace_id, token=_tok(db, token))


def create_decoupe(db: Db, workspace_id: str, entry_request_id: int,
                   parent_task_id: int, steps: List[Dict[str, Any]],
                   token: str = "") -> Dict[str, Any]:
    """Crée une découpe : children + dépendances (chaque enfant dépend du parent)."""
    return L.create_decoupe(db, workspace_id, entry_request_id, parent_task_id,
                            steps, token=_tok(db, token))


def assign_task(db: Db, workspace_id: str, agent_id: int,
                types: Any = None, token: str = "") -> Optional[Dict[str, Any]]:
    """Attribution greedy synchrone (repris ou pick par type+priorité)."""
    return L.assign(db, workspace_id, agent_id, types, token=_tok(db, token))


def ask_new_task(db: Db, workspace_id: str, agent_id: int,
                 types: List[Dict[str, Any]], token: str = "") -> int:
    db.check_write(_tok(db, token))
    rid = db.table("ask_new_task").add(
        {"workspace_id": workspace_id, "agent_id": agent_id,
         "types": json.dumps(types)}, token=_tok(db, token))
    return int(rid)


def set_ask_served(db: Db, ask_id: int, token: str = "") -> None:
    db.table("ask_new_task").update(
        {"id": ask_id}, {"status": "served", "served_at": _now()},
        token=_tok(db, token))


def cancel_ask(db: Db, ask_id: int, token: str = "") -> int:
    return db.table("ask_new_task").update(
        {"id": ask_id}, {"status": "answered_wait"}, token=_tok(db, token))


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
