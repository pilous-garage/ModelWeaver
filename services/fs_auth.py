"""FsAuthManager — autorisations d'accès absolu à l'hôte (V0.6.x).

Les skills `host_*` (host_read/host_write/host_run) accèdent à des chemins
ABSOLUS en dehors du home de l'agent. Chaque accès est vérifié contre une
allowlist par agent stockée dans le domaine `security` (`agent_fs_auth`).

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

from modules.sqlite.security import read as security_read
from modules.sqlite.security import write as security_write


class FsAuthError(PermissionError):
    pass


class FsAuthManager:
    """Façade sur le domaine security (ouvert, sans token).

    `conn` optionnel : connexion sqlite3 de substitution (tests) — la
    table est alors créée dans la connexion fournie.
    """

    def __init__(self, conn: Optional[sqlite3.Connection] = None):
        if conn is not None:
            self._own = None
            self.conn = conn
            self._ensure_schema(conn)
        else:
            self._own = None
            self.conn = None
        # Le domaine security.db est créé/ouvert à la demande par
        # security/get_domain() au premier appel (WAL, pas de token).

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS agent_fs_auth (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id INTEGER NOT NULL,
                root_path TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'r',
                UNIQUE(agent_id, root_path)
            )
        """)
        conn.commit()

    def grant(self, agent_id: int, root_path: str, mode: str = "r") -> None:
        if self.conn is not None:
            self._grant(agent_id, root_path, mode)
            return
        security_write.grant_fs_auth(
            agent_id, os.path.abspath(root_path),
            "rw" if mode == "rw" else "r")

    def revoke(self, agent_id: int, root_path: str) -> None:
        if self.conn is not None:
            self.conn.execute(
                "DELETE FROM agent_fs_auth WHERE agent_id = ? AND root_path = ?",
                (agent_id, os.path.abspath(root_path)))
            self.conn.commit()
            return
        security_write.revoke_fs_auth(agent_id, os.path.abspath(root_path))

    def list(self, agent_id: int) -> List[dict]:
        if self.conn is not None:
            rows = self.conn.execute(
                "SELECT root_path, mode FROM agent_fs_auth WHERE agent_id = ?",
                (agent_id,)).fetchall()
            return [{"root_path": r[0] if isinstance(r, tuple) else r["root_path"],
                     "mode": r[1] if isinstance(r, tuple) else r["mode"]} for r in rows]
        rows = security_read.get_fs_auth(agent_id)
        return [{"root_path": r["root_path"], "mode": r["mode"]} for r in rows]

    def check(self, agent_id: int, abs_path: str, want_write: bool = False) -> bool:
        if self.conn is not None:
            rows = self.conn.execute(
                "SELECT root_path, mode FROM agent_fs_auth WHERE agent_id = ?",
                (agent_id,)).fetchall()
            rows = [dict(r) for r in rows]
        else:
            rows = security_read.get_fs_auth(agent_id)
        target = os.path.abspath(abs_path)
        for r in rows:
            root = r["root_path"]
            if target == root or target.startswith(root.rstrip("/") + "/"):
                if want_write and r["mode"] != "rw":
                    return False
                return True
        return False

    @property
    def _db(self):
        """Accès direct au domaine security (compat interne)."""
        from modules.sqlite.security import get_domain
        return get_domain().db


def _get_manager() -> FsAuthManager:
    return FsAuthManager()