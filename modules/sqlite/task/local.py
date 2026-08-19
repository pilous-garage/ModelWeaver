from __future__ import annotations

import json as _json
from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db, WriteDenied


# ── Helpers identitaires / parsing ─────────────────────────
SUB_TASK_TYPES = ("analysis", "coding", "testing", "review", "merge", "respond")
TASK_STATUSES = ("todo", "waiting_deps", "attributed", "doing", "done", "too_hard",
                 "error", "cancelled")


def parse_types(v: Any) -> List[str]:
    """Parse le paramètre `types` de ask_new_task : liste de sub_task_type
    piochables. Accepte une liste [{type,...}], une chaîne JSON, ou une liste
    de type."""
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = _json.loads(v)
        except ValueError:
            return [v]
    out: List[str] = []
    for it in v or []:
        if isinstance(it, str):
            t = it
        elif isinstance(it, dict):
            t = str(it.get("type", ""))
        else:
            t = str(it)
        if t and t not in out:
            out.append(t)
    return out


def assign(db: Db, workspace_id: str, agent_id: int,
           types: Any = None, token: str = "") -> Optional[Dict[str, Any]]:
    """Attribution synchrone d'une task piochable par type + priorité.

    - reprend les tasks déjà attribuées à `agent_id` (status='attributed') ;
    - sinon pioche la meilleure task (todo | waiting_deps) dont les dépendances
      sont satisfaites, sub_task_type ∈ `types`, meilleure priorité d'abord :
      meilleure priorité, puis plus ancienne ;
    - claim en 'attributed' pour `agent_id` ;
    - sinon None (rien à attribuer → l'agent register_wait pourra être réveillé)."""
    db.check_write(token or db._write_token)
    allowed = parse_types(types)
    r = db.sql(
        "SELECT t.* FROM tasks t WHERE t.workspace_id=? AND t.assigned_to=? "
        "AND t.status='attributed' ORDER BY t.created_at LIMIT 1",
        (workspace_id, str(agent_id)), token=token)
    if not r:
        where = ["t.workspace_id=?", "t.sub_task_type IS NOT NULL"]
        args: List[Any] = [workspace_id]
        if allowed:
            where.append("t.sub_task_type IN (%s)"
                         % ",".join("?" * len(allowed)))
            args += allowed
        deps = ("AND NOT EXISTS ("
                "SELECT 1 FROM task_dependencies d JOIN tasks p "
                "ON p.task_id=d.parent_id AND d.child_id=t.task_id "
                "WHERE p.status!=d.required_state "
                "OR (d.required_tag!='' AND p.tag!=d.required_tag))")
        q = ("SELECT t.* FROM tasks t WHERE " + " AND ".join(where)
             + " AND t.status IN ('todo','waiting_deps') AND t.assigned_to='' "
             + deps + " ORDER BY t.priority DESC, t.created_at LIMIT 1")
        r = db.sql(q, args, token=token)
    if not r:
        return None
    row = r[0]
    db.table("tasks").update({"task_id": row["task_id"]},
                             {"status": "attributed", "assigned_to": str(agent_id)},
                             token=token)
    row = dict(row)
    row.update({"status": "attributed", "assigned_to": str(agent_id)})
    return row


def _waiting_count(db: Db, workspace_id: str = "") -> int:
    w, args = "", []
    if workspace_id:
        w = " AND workspace_id=?"
        args.append(workspace_id)
    deps = ("AND NOT EXISTS ("
            "SELECT 1 FROM task_dependencies d JOIN tasks p "
            "ON p.task_id=d.parent_id AND d.child_id=tasks.task_id "
            "WHERE p.status!=d.required_state "
            "OR (d.required_tag!='' AND p.tag!=d.required_tag))")
    r = db.sql(f"SELECT COUNT(*) AS n FROM tasks WHERE status='waiting_deps'"
               + w + deps, args)
    return r[0]["n"] if r else 0


def release_satisfied_children(db: Db, workspace_id: str = "",
                               token: str = "") -> int:
    """Relâche les waiting_deps dont toutes les dépendances sont satisfaites
    (→ remis en 'todo' pour être piochable). Retourne le nombre libéré."""
    db.check_write(token or db._write_token)
    tok = token or db._write_token
    before = _waiting_count(db, workspace_id)
    w = ""
    args: List[Any] = []
    if workspace_id:
        w = " AND workspace_id=?"
        args.append(workspace_id)
    deps = ("AND NOT EXISTS ("
            "SELECT 1 FROM task_dependencies d JOIN tasks p "
            "ON p.task_id=d.parent_id AND d.child_id=tasks.task_id "
            "WHERE p.status!=d.required_state "
            "OR (d.required_tag!='' AND p.tag!=d.required_tag"
            + "))")
    db.sql(f"UPDATE tasks SET status='todo' WHERE status='waiting_deps'"
           + w + " " + deps, args, token=tok)
    return before - _waiting_count(db, workspace_id)


def create_task(db: Db, workspace_id: str, entry_request_id: int,
                parent_task_id: Optional[int] = None,
                sub_task_type: str = "", title: str = "", description: str = "",
                status: str = "todo", token: str = "") -> Dict[str, Any]:
    """Crée une task (issue-level si parent_task_id=None, sinon sub_task)."""
    db.check_write(token or db._write_token)
    if sub_task_type and sub_task_type not in SUB_TASK_TYPES:
        raise ValueError(f"sub_task_type inconnu: {sub_task_type!r}")
    if status not in TASK_STATUSES:
        raise ValueError(f"status inconnu: {status!r}")
    rid = db.table("tasks").add({
        "workspace_id": workspace_id,
        "entry_request_id": entry_request_id,
        "parent_task_id": parent_task_id,
        "sub_task_type": sub_task_type or None,
        "status": "waiting_deps" if parent_task_id else status,
        "title": title,
        "description": description,
    }, token=token)
    return {"ok": True, "task_id": int(rid),
            "parent_task_id": parent_task_id, "sub_task_type": sub_task_type}


def add_dependency(db: Db, child_id: int, parent_id: int,
                   required_state: str = "done", required_tag: str = "",
                   token: str = "") -> Dict[str, Any]:
    db.check_write(token or db._write_token)
    db.table("task_dependencies").add({
        "child_id": child_id, "parent_id": parent_id,
        "required_state": required_state, "required_tag": required_tag},
        token=token)
    return {"ok": True, "child_id": child_id, "parent_id": parent_id}


def create_decoupe(db: Db, workspace_id: str, entry_request_id: int,
                   parent_task_id: int, steps: List[Dict[str, Any]],
                   token: str = "") -> Dict[str, Any]:
    """Crée une découpe : children + dépendances (chaque enfant dépend du parent)."""
    children: List[int] = []
    for s in steps:
        r = create_task(db, workspace_id, entry_request_id, parent_task_id,
                        s.get("sub_task_type", ""), s.get("title", ""),
                        s.get("description", ""), "waiting_deps",
                        token=token)
        children.append(r["task_id"])
        add_dependency(db, r["task_id"], parent_task_id,
                       required_state="done", required_tag=s.get("tag", ""),
                       token=token)
    return {"ok": True, "children": children}


def resolve_access_path(db: Db, accessor: str) -> Dict[str, Any]:
    """Placeholder : la route d'accès full-text (catalogue.<type>.<ns>.<name>)
    est fournie par le domaine local (local.resolve_path) ; le domaine task
    n'a pas de path symbolic, mais on délègue les refs fichiers au local si besoin.
    Ici : retourne un noyau d'identité de task pour un éventuel lien.
    """
    raise NotImplementedError("accès catalogue via local.resolve_path")