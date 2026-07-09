"""Agent OS — Repositories pour la couche agent.
Suit le même pattern DAO que sql/db.py.
"""

import json
import uuid
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _ref(prefix: str = "agent") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _row_to_dict(row: sqlite3.Row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class ModelProviderRepository:
    """Ressources hardware/cloud pour les agents."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, engine_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if engine_type:
            cur = self.conn.execute(
                "SELECT * FROM model_providers WHERE engine_type = ? ORDER BY name",
                (engine_type,)
            )
        else:
            cur = self.conn.execute("SELECT * FROM model_providers ORDER BY name")
        return _rows_to_list(cur.fetchall())

    def get(self, provider_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM model_providers WHERE provider_id = ?", (provider_id,)
        )
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        cur = self.conn.execute("""
            INSERT INTO model_providers (name, engine_type, model_name, endpoint_url,
                max_concurrent, api_key_ref)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            data["name"], data["engine_type"], data["model_name"],
            data.get("endpoint_url"), data.get("max_concurrent", 1),
            data.get("api_key_ref")
        ))
        return cur.lastrowid

    def increment_concurrent(self, provider_id: int) -> bool:
        cur = self.conn.execute("""
            UPDATE model_providers
            SET current_concurrent = current_concurrent + 1
            WHERE provider_id = ? AND current_concurrent < max_concurrent
        """, (provider_id,))
        return cur.rowcount > 0

    def decrement_concurrent(self, provider_id: int) -> None:
        self.conn.execute("""
            UPDATE model_providers
            SET current_concurrent = CASE WHEN current_concurrent > 0 THEN current_concurrent - 1 ELSE 0 END
            WHERE provider_id = ?
        """, (provider_id,))

    def set_cooldown(self, provider_id: int, until_iso: str) -> None:
        self.conn.execute(
            "UPDATE model_providers SET cooldown_until = ? WHERE provider_id = ?",
            (until_iso, provider_id)
        )

    def delete(self, provider_id: int) -> bool:
        cur = self.conn.execute(
            "DELETE FROM model_providers WHERE provider_id = ?", (provider_id,)
        )
        return cur.rowcount > 0


class AgentRepository:
    """Identité et couplage des agents."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, status: Optional[str] = None,
                 role_type: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = []
        params = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if role_type:
            clauses.append("role_type = ?")
            params.append(role_type)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cur = self.conn.execute(
            f"SELECT a.*, mp.name as provider_name, mp.model_name, mp.engine_type "
            f"FROM agents a "
            f"LEFT JOIN model_providers mp ON mp.provider_id = a.provider_id"
            f"{where} ORDER BY a.name", params
        )
        return _rows_to_list(cur.fetchall())

    def get(self, agent_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT a.*, mp.name as provider_name, mp.model_name, mp.engine_type
            FROM agents a
            LEFT JOIN model_providers mp ON mp.provider_id = a.provider_id
            WHERE a.agent_id = ?
        """, (agent_id,))
        return _row_to_dict(cur.fetchone())

    def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT a.*, mp.name as provider_name, mp.model_name, mp.engine_type
            FROM agents a
            LEFT JOIN model_providers mp ON mp.provider_id = a.provider_id
            WHERE a.name = ?
        """, (name,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        cur = self.conn.execute("""
            INSERT INTO agents (name, role_type, provider_id, status, config_json, state_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            data["name"], data["role_type"], data.get("provider_id"),
            data.get("status", "IDLE"),
            json.dumps(data.get("config", {})) if data.get("config") else None,
            json.dumps(data.get("state", {})) if data.get("state") else None
        ))
        return cur.lastrowid

    def update_status(self, agent_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE agents SET status = ? WHERE agent_id = ?",
            (status, agent_id)
        )

    def save_state(self, agent_id: int, state: Dict[str, Any]) -> None:
        self.conn.execute(
            "UPDATE agents SET state_json = ? WHERE agent_id = ?",
            (json.dumps(state) if state else None, agent_id)
        )

    def load_state(self, agent_id: int) -> Dict[str, Any]:
        cur = self.conn.execute(
            "SELECT state_json FROM agents WHERE agent_id = ?", (agent_id,)
        )
        row = cur.fetchone()
        if row and row["state_json"]:
            return json.loads(row["state_json"])
        return {}

    def set_successor(self, agent_id: int, successor_id: int) -> None:
        self.conn.execute(
            "UPDATE agents SET successor_id = ? WHERE agent_id = ?",
            (successor_id, agent_id)
        )

    def get_successor(self, agent_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT a2.* FROM agents a1
            JOIN agents a2 ON a2.agent_id = a1.successor_id
            WHERE a1.agent_id = ?
        """, (agent_id,))
        return _row_to_dict(cur.fetchone())

    def terminate(self, agent_id: int, successor_id: Optional[int] = None) -> None:
        """Marque l'agent comme TERMINATED avec successeur optionnel."""
        if successor_id:
            self.conn.execute(
                "UPDATE agents SET status='TERMINATED', successor_id=? WHERE agent_id=?",
                (successor_id, agent_id)
            )
        else:
            self.conn.execute(
                "UPDATE agents SET status='TERMINATED' WHERE agent_id=?",
                (agent_id,)
            )

    def delete(self, agent_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        return cur.rowcount > 0


class SessionRepository:
    """Fils de discussion persistants."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, agent_id: Optional[int] = None,
                 status: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = []
        params = []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cur = self.conn.execute(
            f"SELECT * FROM sessions{where} ORDER BY updated_at DESC", params
        )
        return _rows_to_list(cur.fetchall())

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        )
        return _row_to_dict(cur.fetchone())

    def create(self, agent_id: int, context_summary: Optional[str] = None) -> str:
        session_id = _ref("sess")
        now = _now_iso()
        self.conn.execute("""
            INSERT INTO sessions (session_id, agent_id, context_summary, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, agent_id, context_summary, now, now))
        return session_id

    def update_summary(self, session_id: str, summary: str) -> None:
        now = _now_iso()
        self.conn.execute(
            "UPDATE sessions SET context_summary = ?, updated_at = ? WHERE session_id = ?",
            (summary, now, session_id)
        )

    def update_status(self, session_id: str, status: str) -> None:
        now = _now_iso()
        self.conn.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE session_id = ?",
            (status, now, session_id)
        )

    def transfer_to_agent(self, from_agent_id: int, to_agent_id: int,
                          status: str = "ACTIVE") -> int:
        """Transfert les sessions actives d'un agent à un autre (succession)."""
        now = _now_iso()
        cur = self.conn.execute("""
            UPDATE sessions SET agent_id = ?, updated_at = ?
            WHERE agent_id = ? AND status = 'ACTIVE'
        """, (to_agent_id, now, from_agent_id))
        return cur.rowcount


class AgentMessageRepository:
    """Mémoire brute au format OpenAI."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_by_session(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT * FROM agent_messages
            WHERE session_id = ?
            ORDER BY created_at ASC
            LIMIT ?
        """, (session_id, limit))
        return _rows_to_list(cur.fetchall())

    def add(self, session_id: str, role: str, content: str,
            tokens_used: int = 0) -> int:
        cur = self.conn.execute("""
            INSERT INTO agent_messages (session_id, role, content, tokens_used)
            VALUES (?, ?, ?, ?)
        """, (session_id, role, content, tokens_used))
        return cur.lastrowid

    def add_many(self, messages: List[Dict[str, Any]]) -> int:
        """Insert multiple messages. Each dict must have session_id, role, content."""
        now = _now_iso()
        rows = [(m["session_id"], m["role"], m["content"],
                 m.get("tokens_used", 0), now) for m in messages]
        self.conn.executemany("""
            INSERT INTO agent_messages (session_id, role, content, tokens_used, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, rows)
        return len(rows)

    def count_by_session(self, session_id: str) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM agent_messages WHERE session_id = ?",
            (session_id,)
        )
        return cur.fetchone()["cnt"]

    def delete_by_session(self, session_id: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM agent_messages WHERE session_id = ?", (session_id,)
        )
        return cur.rowcount


class WakeupCallRepository:
    """Système nerveux — tâches et réveils."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_pending(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Récupère les tâches mûres (execute_after <= now) avec BEGIN IMMEDIATE."""
        now = _now_iso()
        cur = self.conn.execute("""
            SELECT * FROM wakeup_calls
            WHERE status = 'TODO' AND execute_after <= ?
            ORDER BY execute_after ASC
            LIMIT ?
        """, (now, limit))
        return _rows_to_list(cur.fetchall())

    def claim(self, task_id: int) -> bool:
        """Marque une tâche comme BUSY (UPDATE atomique sur status='TODO')."""
        cur = self.conn.execute(
            "UPDATE wakeup_calls SET status = 'BUSY' WHERE task_id = ? AND status = 'TODO'",
            (task_id,)
        )
        return cur.rowcount > 0

    def complete(self, task_id: int, result_summary: Optional[str] = None) -> None:
        self.conn.execute(
            "UPDATE wakeup_calls SET status = 'COMPLETED', result_summary = ? WHERE task_id = ?",
            (result_summary, task_id)
        )

    def update_sleep(self, task_id: int, seconds: int, next_step_id: Optional[str]) -> None:
        """Programme le réveil de la tâche après N secondes et mémorise l'étape suivante."""
        now = _now_iso()
        # On calcule l'heure de réveil (approximation simple en ISO)
        # Pour être précis on pourrait utiliser datetime, mais on reste cohérent avec _now_iso()
        from datetime import datetime, timedelta, timezone
        wakeup_time = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
        
        self.conn.execute("""
            UPDATE wakeup_calls SET 
                status = 'TODO', 
                execute_after = ?, 
                request_payload = json_insert(ifnull(request_payload, '{}'), '$.next_step_id', ?)
            WHERE task_id = ?
        """, (wakeup_time, next_step_id, task_id))

    def fail(self, task_id: int, reason: Optional[str] = None) -> None:
        self.conn.execute(
            "UPDATE wakeup_calls SET status = 'FAILED', result_summary = ? WHERE task_id = ?",
            (reason, task_id)
        )

    def create(self, agent_id: int, session_id: str, skill: str,
               request_payload: Optional[str] = None,
               execute_after: Optional[str] = None) -> int:
        cur = self.conn.execute("""
            INSERT INTO wakeup_calls (agent_id, session_id, skill, request_payload, execute_after)
            VALUES (?, ?, ?, ?, ?)
        """, (
            agent_id, session_id, skill, request_payload,
            execute_after or _now_iso()
        ))
        return cur.lastrowid

    def reset_busy(self) -> int:
        """Anti-fantôme : réinitialise les tâches BUSY vers TODO (crash recovery)."""
        cur = self.conn.execute(
            "UPDATE wakeup_calls SET status = 'TODO' WHERE status = 'BUSY'"
        )
        return cur.rowcount

    def list_by_agent(self, agent_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT * FROM wakeup_calls
            WHERE agent_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (agent_id, limit))
        return _rows_to_list(cur.fetchall())

# ... (previous code)
    def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute(
            "SELECT * FROM wakeup_calls WHERE task_id = ?", (task_id,)
        )
        return _row_to_dict(cur.fetchone())


class ScheduledJobRepository:
    """Gestion des tâches récurrentes et planifiées."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_due(self) -> List[Dict[str, Any]]:
        """Récupère les jobs activés dont la date de run est passée."""
        now = _now_iso()
        cur = self.conn.execute("""
            SELECT * FROM scheduled_jobs 
            WHERE enabled = 1 AND next_run_at <= ?
            ORDER BY next_run_at ASC
        """, (now,))
        return _rows_to_list(cur.fetchall())

    def save(self, data: Dict[str, Any]) -> int:
        cur = self.conn.execute("""
            INSERT INTO scheduled_jobs (agent_id, role_type, skill, request_payload, 
                                       interval_seconds, next_run_at, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("agent_id"), data.get("role_type"), data["skill"],
            data.get("request_payload"), data.get("interval_seconds", 0),
            data["next_run_at"], data.get("enabled", 1)
        ))
        return cur.lastrowid

    def update_next_run(self, job_id: int, next_run_at: str) -> None:
        self.conn.execute(
            "UPDATE scheduled_jobs SET next_run_at = ? WHERE job_id = ?",
            (next_run_at, job_id)
        )

    def get(self, job_id: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM scheduled_jobs WHERE job_id = ?", (job_id,))
        return _row_to_dict(cur.fetchone())

    def delete(self, job_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM scheduled_jobs WHERE job_id = ?", (job_id,))
        return cur.rowcount > 0
