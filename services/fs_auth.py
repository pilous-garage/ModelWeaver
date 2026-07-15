"""FsAuthManager — autorisations d'accès absolu à l'hôte (V0.6.x).

Les skills `host_*` (host_read/host_write/host_run) accèdent à des chemins
ABSOLUS en dehors du home de l'agent. Chaque accès est vérifié contre une
allowlist par agent stockée en BDD (`agent_fs_auth`).

  - root_path : racine autorisée (le chemin demandé doit être dedans)
  - mode      : 'r' (lecture seule) ou 'rw' (lecture + écriture)

Vérifié à CHAQUE appel de skill host_*. Par défaut aucun accès hôte n'est
autorisé tant que l'agent n'a pas de grant.

(Phase ultérieure : types avancés — liste auto 1re utilisation, envoi
agent→agent, perm temporaire, perm permanent + dossiers dangereux interdits.)
"""

import os
import sqlite3
from typing import List, Optional


class FsAuthError(PermissionError):
    pass


class FsAuthManager:
    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        if conn is None:
            from modules.sql.db import AgentsDB
            self._own = AgentsDB()
            self.conn = self._own.conn
        else:
            self._own = None
            self.conn = conn
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_fs_auth (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                root_path TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'r',
                UNIQUE(agent_id, root_path)
            )
        """)
        self.conn.commit()

    def grant(self, agent_id: int, root_path: str, mode: str = "r") -> None:
        root = os.path.abspath(root_path)
        mode = "rw" if mode == "rw" else "r"
        self.conn.execute(
            "INSERT OR REPLACE INTO agent_fs_auth (agent_id, root_path, mode) "
            "VALUES (?, ?, ?)",
            (agent_id, root, mode))
        self.conn.commit()

    def revoke(self, agent_id: int, root_path: str) -> None:
        root = os.path.abspath(root_path)
        self.conn.execute(
            "DELETE FROM agent_fs_auth WHERE agent_id = ? AND root_path = ?",
            (agent_id, root))
        self.conn.commit()

    def list(self, agent_id: int) -> List[dict]:
        rows = self.conn.execute(
            "SELECT root_path, mode FROM agent_fs_auth WHERE agent_id = ?",
            (agent_id,)).fetchall()
        return [{"root_path": r["root_path"], "mode": r["mode"]} for r in rows]

    def check(self, agent_id: int, abs_path: str, want_write: bool = False) -> bool:
        target = os.path.abspath(abs_path)
        rows = self.conn.execute(
            "SELECT root_path, mode FROM agent_fs_auth WHERE agent_id = ?",
            (agent_id,)).fetchall()
        for r in rows:
            root = r["root_path"]
            if target == root or target.startswith(root.rstrip("/") + "/"):
                if want_write and r["mode"] != "rw":
                    return False
                return True
        return False

    def close(self) -> None:
        if self._own is not None:
            self._own.close()


def _get_manager() -> FsAuthManager:
    return FsAuthManager()
