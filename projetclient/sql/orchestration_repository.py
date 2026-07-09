"""Orchestration — Repositories pour la messagerie, chatroom et todo partagé.

Tables : agent_queue, chatroom_messages, shared_tasks, watchers.
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional

from sql.agent_repository import _now_iso, _row_to_dict, _rows_to_list


class AgentQueueRepository:
    """Queue de messages inter-agents (direct + broadcast + topics)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def send_direct(self, from_id: int, to_id: int, content: str,
                    message_type: str = "text") -> int:
        cur = self.conn.execute("""
            INSERT INTO agent_queue (from_agent_id, to_agent_id, content, message_type)
            VALUES (?, ?, ?, ?)
        """, (from_id, to_id, content, message_type))
        return cur.lastrowid

    def send_broadcast(self, from_id: int, content: str,
                       topic: Optional[str] = None,
                       message_type: str = "broadcast") -> int:
        cur = self.conn.execute("""
            INSERT INTO agent_queue (from_agent_id, to_agent_id, topic, content, message_type)
            VALUES (?, NULL, ?, ?, ?)
        """, (from_id, topic, content, message_type))
        return cur.lastrowid

    def poll_inbox(self, agent_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Messages directs + broadcasts pour un agent."""
        cur = self.conn.execute("""
            SELECT q.*, a.name as from_agent_name
            FROM agent_queue q
            LEFT JOIN agents a ON a.agent_id = q.from_agent_id
            WHERE q.status = 'TODO'
              AND (q.to_agent_id = ? OR q.to_agent_id IS NULL)
            ORDER BY q.created_at ASC
            LIMIT ?
        """, (agent_id, limit))
        return _rows_to_list(cur.fetchall())

    def mark_read(self, queue_id: int) -> None:
        self.conn.execute(
            "UPDATE agent_queue SET status = 'READ' WHERE queue_id = ?", (queue_id,)
        )

    def mark_archived(self, queue_id: int) -> None:
        self.conn.execute(
            "UPDATE agent_queue SET status = 'ARCHIVED' WHERE queue_id = ?", (queue_id,)
        )

    def count_unread(self, agent_id: int) -> int:
        cur = self.conn.execute("""
            SELECT COUNT(*) as cnt FROM agent_queue
            WHERE status = 'TODO'
              AND (to_agent_id = ? OR to_agent_id IS NULL)
        """, (agent_id,))
        return cur.fetchone()["cnt"]


class ChatroomRepository:
    """Board public avec threads."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def post(self, agent_id: int, content: str,
             thread_id: Optional[int] = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO chatroom_messages (agent_id, thread_id, content)
            VALUES (?, ?, ?)
        """, (agent_id, thread_id, content))
        return cur.lastrowid

    def get_thread(self, message_id: int) -> List[Dict[str, Any]]:
        """Récupère un message et toutes ses réponses."""
        cur = self.conn.execute("""
            SELECT cm.*, a.name as agent_name
            FROM chatroom_messages cm
            LEFT JOIN agents a ON a.agent_id = cm.agent_id
            WHERE cm.message_id = ? OR cm.thread_id = ?
            ORDER BY cm.created_at ASC
        """, (message_id, message_id))
        return _rows_to_list(cur.fetchall())

    def get_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Derniers messages top-level (pas des réponses)."""
        cur = self.conn.execute("""
            SELECT cm.*, a.name as agent_name,
                   (SELECT COUNT(*) FROM chatroom_messages r WHERE r.thread_id = cm.message_id) as replies
            FROM chatroom_messages cm
            LEFT JOIN agents a ON a.agent_id = cm.agent_id
            WHERE cm.thread_id IS NULL
            ORDER BY cm.created_at DESC
            LIMIT ?
        """, (limit,))
        return _rows_to_list(cur.fetchall())

    def get_by_agent(self, agent_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT * FROM chatroom_messages
            WHERE agent_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (agent_id, limit))
        return _rows_to_list(cur.fetchall())

    def count_since(self, since_iso: str) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM chatroom_messages WHERE created_at > ?",
            (since_iso,)
        )
        return cur.fetchone()["cnt"]


class SharedTaskRepository:
    """Todo partagé entre agents."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, title: str, description: Optional[str] = None,
               required_role: Optional[str] = None,
               context: str = "general",
               priority: int = 0,
               parent_task_id: Optional[int] = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO shared_tasks (title, description, required_role, context, priority, parent_task_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (title, description, required_role, context, priority, parent_task_id))
        return cur.lastrowid

    def list_pending(self, role: Optional[str] = None,
                     context: Optional[str] = None,
                     limit: int = 20) -> List[Dict[str, Any]]:
        clauses = ["st.status = 'TODO'"]
        params = []
        if role:
            clauses.append("(required_role IS NULL OR required_role = ?)")
            params.append(role)
        if context:
            clauses.append("context = ?")
            params.append(context)
        where = " AND ".join(clauses)
        cur = self.conn.execute(f"""
            SELECT st.*, a.name as assigned_name
            FROM shared_tasks st
            LEFT JOIN agents a ON a.agent_id = st.assigned_to
            WHERE {where}
            ORDER BY st.priority DESC, st.created_at ASC
            LIMIT ?
        """, params + [limit])
        return _rows_to_list(cur.fetchall())

    def claim(self, task_id: int, agent_id: int) -> bool:
        """Tente d'assigner une tâche (UPDATE atomique sur status='TODO')."""
        now = _now_iso()
        cur = self.conn.execute("""
            UPDATE shared_tasks SET status = 'IN_PROGRESS', assigned_to = ?, updated_at = ?
            WHERE task_id = ? AND status = 'TODO'
        """, (agent_id, now, task_id))
        return cur.rowcount > 0

    def complete(self, task_id: int, result: Optional[str] = None) -> None:
        now = _now_iso()
        self.conn.execute(
            "UPDATE shared_tasks SET status = 'DONE', updated_at = ? WHERE task_id = ?",
            (now, task_id)
        )

    def fail(self, task_id: int, reason: Optional[str] = None) -> None:
        now = _now_iso()
        self.conn.execute(
            "UPDATE shared_tasks SET status = 'FAILED', updated_at = ? WHERE task_id = ?",
            (now, task_id)
        )

    def release(self, task_id: int) -> None:
        now = _now_iso()
        self.conn.execute("""
            UPDATE shared_tasks SET status = 'TODO', assigned_to = NULL, updated_at = ?
            WHERE task_id = ?
        """, (now, task_id))

    def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT st.*, a.name as assigned_name
            FROM shared_tasks st
            LEFT JOIN agents a ON a.agent_id = st.assigned_to
            WHERE st.task_id = ?
        """, (task_id,))
        return _row_to_dict(cur.fetchone())

    def list_done_without_review(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Tâches DONE qui n'ont pas d'enfant de type 'critique'."""
        cur = self.conn.execute("""
            SELECT * FROM shared_tasks st
            WHERE st.status = 'DONE'
              AND NOT EXISTS (
                SELECT 1 FROM shared_tasks child 
                WHERE child.parent_task_id = st.task_id 
                  AND child.required_role = 'critique'
              )
            ORDER BY st.updated_at ASC
            LIMIT ?
        """, (limit,))
        return _rows_to_list(cur.fetchall())

    def list_by_agent(self, agent_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT * FROM shared_tasks
            WHERE assigned_to = ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (agent_id, limit))
        return _rows_to_list(cur.fetchall())

    def count_pending(self, role: Optional[str] = None,
                      context: Optional[str] = None) -> int:
        clauses = ["status = 'TODO'"]
        params = []
        if role:
            clauses.append("(required_role IS NULL OR required_role = ?)")
            params.append(role)
        if context:
            clauses.append("context = ?")
            params.append(context)
        cur = self.conn.execute(
            f"SELECT COUNT(*) as cnt FROM shared_tasks WHERE {' AND '.join(clauses)}",
            params
        )
        return cur.fetchone()["cnt"]


class WatcherRepository:
    """Surveillants automatiques."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, agent_id: int, watch_type: str,
               filter_criteria: Optional[Dict] = None,
               interval_seconds: int = 60) -> int:
        cur = self.conn.execute("""
            INSERT INTO watchers (agent_id, watch_type, filter_criteria, interval_seconds)
            VALUES (?, ?, ?, ?)
        """, (
            agent_id, watch_type,
            json.dumps(filter_criteria) if filter_criteria else None,
            interval_seconds,
        ))
        return cur.lastrowid

    def list_due(self, now_iso: str) -> List[Dict[str, Any]]:
        """Watchers dont le délai est écoulé."""
        cur = self.conn.execute("""
            SELECT w.*, a.name as agent_name, a.role_type
            FROM watchers w
            JOIN agents a ON a.agent_id = w.agent_id
            WHERE w.enabled = 1
              AND (w.last_checked_at IS NULL OR w.last_checked_at <= ?)
              AND a.status = 'IDLE'
            ORDER BY w.last_checked_at ASC NULLS FIRST
        """, (now_iso,))
        return _rows_to_list(cur.fetchall())

    def mark_checked(self, watcher_id: int) -> None:
        now = _now_iso()
        self.conn.execute(
            "UPDATE watchers SET last_checked_at = ? WHERE watcher_id = ?",
            (now, watcher_id)
        )

    def update_interval(self, watcher_id: int, interval_seconds: int) -> None:
        self.conn.execute(
            "UPDATE watchers SET interval_seconds = ? WHERE watcher_id = ?",
            (interval_seconds, watcher_id)
        )

    def delete(self, watcher_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM watchers WHERE watcher_id = ?", (watcher_id,)
        )
        return cur.rowcount > 0

    def list_by_agent(self, agent_id: int) -> List[Dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM watchers WHERE agent_id = ? ORDER BY watch_type",
            (agent_id,)
        )
        return _rows_to_list(cur.fetchall())


class ConnectionRepository:
    """Branchements persistants d'un agent (chatroom, todo, queue, files, API, agents)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def connect(self, agent_id: int, channel: str,
                target_id: Optional[int] = None,
                config: Optional[Dict] = None) -> int:
        """Crée ou remet enabled une connexion."""
        existing = self.get(agent_id, channel, target_id)
        if existing:
            self.conn.execute(
                "UPDATE agent_connections SET enabled = 1, config_json = ? WHERE conn_id = ?",
                (json.dumps(config) if config else None, existing["conn_id"])
            )
            return existing["conn_id"]

        cur = self.conn.execute("""
            INSERT INTO agent_connections (agent_id, channel, target_id, config_json)
            VALUES (?, ?, ?, ?)
        """, (
            agent_id, channel, target_id,
            json.dumps(config) if config else None,
        ))
        return cur.lastrowid

    def disconnect(self, agent_id: int, channel: str,
                   target_id: Optional[int] = None) -> bool:
        """Désactive une connexion (enabled=0)."""
        if target_id is not None:
            cur = self.conn.execute("""
                UPDATE agent_connections SET enabled = 0
                WHERE agent_id = ? AND channel = ? AND target_id = ?
            """, (agent_id, channel, target_id))
        else:
            cur = self.conn.execute("""
                UPDATE agent_connections SET enabled = 0
                WHERE agent_id = ? AND channel = ? AND target_id IS NULL
            """, (agent_id, channel))
        return cur.rowcount > 0

    def delete(self, conn_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM agent_connections WHERE conn_id = ?", (conn_id,)
        )
        return cur.rowcount > 0

    def get(self, agent_id: int, channel: str,
            target_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        if target_id is not None:
            cur = self.conn.execute(
                "SELECT * FROM agent_connections WHERE agent_id=? AND channel=? AND target_id=?",
                (agent_id, channel, target_id)
            )
        else:
            cur = self.conn.execute(
                "SELECT * FROM agent_connections WHERE agent_id=? AND channel=? AND target_id IS NULL",
                (agent_id, channel)
            )
        row = cur.fetchone()
        if row:
            row = dict(row)
            if row.get("config_json"):
                row["config"] = json.loads(row["config_json"])
        return row

    def list_by_agent(self, agent_id: int, enabled_only: bool = False) -> List[Dict[str, Any]]:
        clause = "WHERE agent_id = ?"
        params = [agent_id]
        if enabled_only:
            clause += " AND enabled = 1"
        cur = self.conn.execute(
            f"SELECT * FROM agent_connections {clause} ORDER BY channel", params
        )
        rows = _rows_to_list(cur.fetchall())
        for r in rows:
            if r.get("config_json"):
                r["config"] = json.loads(r["config_json"])
        return rows

    def list_by_channel(self, channel: str, enabled_only: bool = True) -> List[Dict[str, Any]]:
        clause = "WHERE channel = ?"
        params = [channel]
        if enabled_only:
            clause += " AND enabled = 1"
        cur = self.conn.execute(
            f"SELECT * FROM agent_connections {clause}", params
        )
        return _rows_to_list(cur.fetchall())
