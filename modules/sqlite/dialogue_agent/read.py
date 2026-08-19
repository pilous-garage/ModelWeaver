"""read — lectures du domaine dialogue_agent (bridges, agents, API).
    Rien d'écrit ici : ouvrir db_ro() (lecture seule)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db

Rows = List[Dict[str, Any]]


def _rs(rows) -> Rows:
    return [dict(r) for r in rows]


def _r(row) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


# ── conversations (chatroom arborescent) ────────────────────────────────────

def conversation_get(d: Db, conv_id: int) -> Optional[Dict[str, Any]]:
    return _r(d._conn.execute(
        "SELECT * FROM conversation WHERE conv_id = ?", (conv_id,)).fetchone())


def conversation_children(d: Db, conv_id: int) -> Rows:
    """Forks (sous-conversations) d'une conversation."""
    return _rs(d._conn.execute(
        "SELECT * FROM conversation WHERE parent_conv_id = ? "
        "ORDER BY created_at", (conv_id,)).fetchall())


def conversations_by_project(d: Db, project_id: str,
                             conv_type: str = "") -> Rows:
    if conv_type:
        return _rs(d._conn.execute(
            "SELECT * FROM conversation WHERE project_id = ? AND conv_type = ? "
            "ORDER BY created_at", (project_id, conv_type)).fetchall())
    return _rs(d._conn.execute(
        "SELECT * FROM conversation WHERE project_id = ? "
        "ORDER BY created_at", (project_id,)).fetchall())


def messages(d: Db, conv_id: int, limit: int = 50) -> Rows:
    return _rs(d._conn.execute(
        "SELECT * FROM message WHERE conv_id = ? ORDER BY created_at DESC "
        "LIMIT ?", (conv_id, limit)).fetchall())


def message_thread(d: Db, conv_id: int) -> Rows:
    """Fil complet d'une conversation : messages de la conv + de TOUTES ses
    sous-conversations (fork de fils, arborescence récursive)."""
    return _rs(d._conn.execute(
        "WITH RECURSIVE convs AS ("
        "  SELECT conv_id FROM conversation WHERE conv_id = ?"
        "  UNION ALL"
        "  SELECT c.conv_id FROM conversation c "
        "  JOIN convs ON c.parent_conv_id = convs.conv_id"
        ") SELECT m.* FROM message m "
        "WHERE m.conv_id IN (SELECT conv_id FROM convs) "
        "ORDER BY m.created_at", (conv_id,)).fetchall())


# ── direct 1:1 ──────────────────────────────────────────────────────────────

def direct_inbox(d: Db, agent_to: int, received: Optional[int] = None,
                 limit: int = 100) -> Rows:
    if received is None:
        return _rs(d._conn.execute(
            "SELECT * FROM direct_message WHERE agent_to = ? "
            "ORDER BY created_at DESC LIMIT ?", (agent_to, limit)).fetchall())
    return _rs(d._conn.execute(
        "SELECT * FROM direct_message WHERE agent_to = ? AND received = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (agent_to, received, limit)).fetchall())


def direct_sent(d: Db, agent_from: int, limit: int = 100) -> Rows:
    return _rs(d._conn.execute(
        "SELECT * FROM direct_message WHERE agent_from = ? "
        "ORDER BY created_at DESC LIMIT ?", (agent_from, limit)).fetchall())


def direct_unreceived_count(d: Db, agent_to: int) -> int:
    r = d._conn.execute(
        "SELECT COUNT(*) AS n FROM direct_message "
        "WHERE agent_to = ? AND received = 0", (agent_to,)).fetchone()
    return int(r["n"])


# ── humains (points de terminaison, agent_id NÉGATIF) ───────────────────────

def human_get(d: Db, agent_id: int) -> Optional[Dict[str, Any]]:
    return _r(d._conn.execute(
        "SELECT * FROM humain_as_agent WHERE agent_id = ?",
        (agent_id,)).fetchone())


def humans_all(d: Db) -> Rows:
    return _rs(d._conn.execute(
        "SELECT * FROM humain_as_agent ORDER BY agent_id").fetchall())


# ── escalade humain ─────────────────────────────────────────────────────────

def human_choice_get(d: Db, choice_id: str) -> Optional[Dict[str, Any]]:
    return _r(d._conn.execute(
        "SELECT * FROM human_choice WHERE choice_id = ?",
        (choice_id,)).fetchone())


def human_choices(d: Db, status: str = "pending") -> Rows:
    return _rs(d._conn.execute(
        "SELECT * FROM human_choice WHERE status = ? ORDER BY asked_at",
        (status,)).fetchall())


def human_choices_for_task(d: Db, task_id: int) -> Rows:
    return _rs(d._conn.execute(
        "SELECT * FROM human_choice WHERE task_id = ? ORDER BY asked_at",
        (task_id,)).fetchall())


# ── consensus ───────────────────────────────────────────────────────────────

def question_get(d: Db, id_question: int) -> Optional[Dict[str, Any]]:
    return _r(d._conn.execute(
        "SELECT * FROM question WHERE id_question = ?",
        (id_question,)).fetchone())


def questions_open(d: Db, project_id: str = "") -> Rows:
    if project_id:
        return _rs(d._conn.execute(
            "SELECT * FROM question WHERE project_id = ? AND "
            "status_answering = 'awaiting' ORDER BY created_at",
            (project_id,)).fetchall())
    return _rs(d._conn.execute(
        "SELECT * FROM question WHERE status_answering = 'awaiting' "
        "ORDER BY created_at").fetchall())


def reponses_of(d: Db, id_question: int) -> Rows:
    return _rs(d._conn.execute(
        "SELECT * FROM reponse WHERE id_question = ? ORDER BY created_at",
        (id_question,)).fetchall())