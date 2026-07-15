"""Audit trail — traçage des opérations sensibles.

Usage:
    from services.audit import audit
    audit("agent.create", agent_id=42, role="assistant", ok=True)
    audit("keys.set", key_ref="openai", ok=False, error="keyring_locked")
"""

import json
import threading
import time
from typing import Any, Dict, Optional

from modules.sql.db import RuntimeDB


class AuditLogger:
    """Logger d'audit persistant dans RuntimeDB.

    Table audit_log : ts, service, action, actor, payload_json, ok.
    Thread-safe.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._db: Optional[RuntimeDB] = None

    def _get_db(self) -> RuntimeDB:
        if self._db is None:
            self._db = RuntimeDB()
            self._db.conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    service TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT DEFAULT '',
                    payload_json TEXT DEFAULT '{}',
                    ok INTEGER DEFAULT 1
                )
            """)
            self._db.conn.execute("""
                CREATE INDEX IF NOT EXISTS ix_audit_action ON audit_log (action)
            """)
            self._db.conn.execute("""
                CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log (ts)
            """)
            self._db.conn.commit()
        return self._db

    def log(self, action: str, service: str = "daemon",
            actor: str = "", ok: bool = True,
            payload: Optional[Dict[str, Any]] = None):
        with self._lock:
            db = self._get_db()
            db.conn.execute(
                "INSERT INTO audit_log (ts, service, action, actor, payload_json, ok) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (int(time.time()), service, action, actor,
                 json.dumps(payload or {}), 1 if ok else 0))
            db.conn.commit()


# Singleton
_audit = AuditLogger()


def audit(action: str, service: str = "daemon",
          actor: str = "", ok: bool = True,
          **payload):
    _audit.log(action, service=service, actor=actor, ok=ok, payload=payload)
