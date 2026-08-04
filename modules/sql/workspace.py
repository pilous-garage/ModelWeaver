"""Workspace DB — projets, tâches, échanges inter-agents.

Une base unique pour tous les workspaces, chargement paresseux :
les requêtes ne lisent que les pages SQLite concernées.

Usage:
    db = WorkspaceDB()
    for ws in db.workspaces.list():
        print(ws["name"])
    tasks = db.for_workspace("proj42").tasks.list_pending()
    db.close()
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from services._common import mw_home


def _default_workspace_db() -> Path:
    return mw_home() / "workspace.db"


def _row(row):
    return dict(row) if row else None


def _rows(rows):
    return [dict(r) for r in rows]


# Hiérarchie de capabilité : un niveau peut traiter les tâches de son niveau
# et des niveaux inférieurs (senior > mid > junior). Pour chaque catégorie de
# rôle (coder, tester, reviewer...), on étend le niveau demandé vers le bas.
_LEVEL_RANK = {"junior": 0, "mid": 1, "senior": 2}


def _compatible_roles(role_required: str) -> List[str]:
    """Rôles compatibles pour un rôle demandé (hiérarchie de capabilité).

    Ex. 'coder_senior' → [coder_senior, coder_mid, coder_junior].
    Ex. 'tester' (sans niveau) → [tester]. Ex. '' → [] (toutes tâches).
    """
    if not role_required:
        return []
    if "_" not in role_required:
        return [role_required]
    base, level = role_required.rsplit("_", 1)
    rank = _LEVEL_RANK.get(level)
    if rank is None:
        return [role_required]
    levels = [lv for lv, r in sorted(_LEVEL_RANK.items(), key=lambda x: x[1])
              if r <= rank]
    return [f"{base}_{lv}" for lv in levels]


# ── Repositories ──────────────────────────────────


class WorkspaceRepository:
    """Méta des workspaces (liste légère, pas de chargement lourd)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list(self) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT workspace_id, name, description, director, "
            "git_shared, created_at, last_activity_at "
            "FROM workspaces ORDER BY last_activity_at DESC"
        ).fetchall())

    def get(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?",
            (workspace_id,)
        ).fetchone())

    def create(self, workspace_id: str, name: str, description: str = "",
               director: Optional[str] = None, git_shared: str = "") -> Dict[str, Any]:
        now = datetime.utcnow().isoformat()
        self.conn.execute("""
            INSERT INTO workspaces (workspace_id, name, description, director,
                                    git_shared, created_at, last_activity_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (workspace_id, name, description, director, git_shared, now, now))
        self.conn.commit()
        return self.get(workspace_id)

    def touch(self, workspace_id: str) -> None:
        self.conn.execute(
            "UPDATE workspaces SET last_activity_at = ? WHERE workspace_id = ?",
            (datetime.utcnow().isoformat(), workspace_id))
        self.conn.commit()

    def delete(self, workspace_id: str) -> None:
        self.conn.execute("DELETE FROM workspaces WHERE workspace_id = ?",
                          (workspace_id,))
        self.conn.commit()


class WorkspaceConfigRepository:
    """Configuration clef/valeur par workspace."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, workspace_id: str, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM workspace_config WHERE workspace_id = ? AND key = ?",
            (workspace_id, key)).fetchone()
        return row[0] if row else None

    def set(self, workspace_id: str, key: str, value: str) -> None:
        self.conn.execute("""
            INSERT OR REPLACE INTO workspace_config (workspace_id, key, value)
            VALUES (?, ?, ?)
        """, (workspace_id, key, value))
        self.conn.commit()

    def delete(self, workspace_id: str, key: str) -> None:
        self.conn.execute(
            "DELETE FROM workspace_config WHERE workspace_id = ? AND key = ?",
            (workspace_id, key))
        self.conn.commit()

    def all(self, workspace_id: str) -> Dict[str, str]:
        rows = self.conn.execute(
            "SELECT key, value FROM workspace_config WHERE workspace_id = ?",
            (workspace_id,)).fetchall()
        return {r["key"]: r["value"] for r in rows}


class TaskRepository:
    """Tâches d'un workspace."""

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id

    def list_pending(self) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM tasks WHERE workspace_id = ? AND status = 'pending' "
            "ORDER BY priority DESC, created_at", (self.wid,)).fetchall())

    def list_all(self) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM tasks WHERE workspace_id = ? "
            "ORDER BY status, priority DESC, created_at",
            (self.wid,)).fetchall())

    def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM tasks WHERE task_id = ? AND workspace_id = ?",
            (task_id, self.wid)).fetchone())

    def create(self, title: str, description: str = "",
               priority: int = 0, parent_id: int = None,
               difficulty: str = "medium", role_required: str = "",
               team_id: int = -1) -> Dict[str, Any]:
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute("""
            INSERT INTO tasks (workspace_id, title, description, priority,
                               parent_id, difficulty, role_required, team_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.wid, title, description, priority, parent_id,
              difficulty, role_required, team_id, now, now))
        self.conn.commit()
        return self.get(cur.lastrowid)

    def claim_next(self, role_required: str = "",
                   exclude_assigned: tuple = (),
                   team_id: int = -1) -> Optional[Dict[str, Any]]:
        """Pioche la prochaine tâche dispo pour un rôle (greedy).

        Hiérarchie de capabilité : un rôle `X_senior` peut piocher les tâches
        X_senior, X_mid et X_junior ; X_mid pioche X_mid + X_junior ; X_junior
        ne pioche que X_junior. Ne pioche que les tâches de la team (ou -1 =
        espace projet partagé). Retourne la tâche 'pending' la plus prioritaire
        compatible, la passe en 'running'. Atomique.
        """
        roles = _compatible_roles(role_required)
        sel_args = [self.wid]
        sel = "SELECT * FROM tasks WHERE workspace_id = ? AND status = 'pending'"
        # team_id : -1 (projet) OU la team de l'agent
        sel += " AND (team_id = ? OR team_id = -1)"
        sel_args.append(team_id)
        if roles:
            ph = ",".join("?" for _ in roles)
            sel += f" AND role_required IN ({ph})"
            sel_args.extend(roles)
        if exclude_assigned:
            ph = ",".join("?" for _ in exclude_assigned)
            sel += f" AND COALESCE(assigned_to,'') NOT IN ({ph})"
            sel_args.extend(exclude_assigned)
        sel += " ORDER BY priority DESC, created_at LIMIT 1"
        row = _row(self.conn.execute(sel, sel_args).fetchone())
        if not row:
            return None
        cur = self.conn.execute(
            "UPDATE tasks SET status = 'running', updated_at = ? "
            "WHERE task_id = ? AND workspace_id = ? AND status = 'pending'",
            (datetime.utcnow().isoformat(), row["task_id"], self.wid))
        self.conn.commit()
        return self.get(row["task_id"]) if cur.rowcount else None

    def update(self, task_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return self.get(task_id)
        sets.append("updated_at = ?")
        vals.append(datetime.utcnow().isoformat())
        vals.extend([task_id, self.wid])
        self.conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} "
            "WHERE task_id = ? AND workspace_id = ?", vals)
        self.conn.commit()
        return self.get(task_id)

    def claim(self, task_id: int, agent_name: str) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET status = 'running', assigned_to = ?, updated_at = ? "
            "WHERE task_id = ? AND workspace_id = ? AND status = 'pending'",
            (agent_name, datetime.utcnow().isoformat(), task_id, self.wid))
        self.conn.commit()
        return cur.rowcount > 0

    def done(self, task_id: int, branch: str = "",
             commit_hash: str = "") -> Optional[Dict[str, Any]]:
        self.conn.execute("""
            UPDATE tasks SET status = 'done', branch = ?, commit_hash = ?,
                            updated_at = ?
            WHERE task_id = ? AND workspace_id = ?
        """, (branch, commit_hash, datetime.utcnow().isoformat(),
              task_id, self.wid))
        self.conn.commit()
        return self.get(task_id)

    def add_file(self, task_id: int, path: str, role: str = "source") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO task_files (task_id, path, role) VALUES (?, ?, ?)",
            (task_id, path, role))
        self.conn.commit()

    def get_files(self, task_id: int) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM task_files WHERE task_id = ?", (task_id,)).fetchall())


class IssueRepository:
    """Issues d'un workspace (demandes de haut niveau → découpées en tâches)."""

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id

    def list_open(self) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM issues WHERE workspace_id = ? AND status IN ('open','analysing','analyzed') "
            "ORDER BY priority DESC, created_at", (self.wid,)).fetchall())

    def list_all(self) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM issues WHERE workspace_id = ? ORDER BY priority DESC, created_at",
            (self.wid,)).fetchall())

    def get(self, issue_id: int) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM issues WHERE issue_id = ? AND workspace_id = ?",
            (issue_id, self.wid)).fetchone())

    def create(self, title: str, description: str = "",
               priority: int = 0, parent_id: int = None,
               team_id: int = -1) -> Dict[str, Any]:
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute("""
            INSERT INTO issues (workspace_id, title, description, priority,
                                parent_id, team_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.wid, title, description, priority, parent_id, team_id, now, now))
        self.conn.commit()
        return self.get(cur.lastrowid)

    def update(self, issue_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return self.get(issue_id)
        sets.append("updated_at = ?")
        vals.append(datetime.utcnow().isoformat())
        vals.extend([issue_id, self.wid])
        self.conn.execute(
            f"UPDATE issues SET {', '.join(sets)} "
            "WHERE issue_id = ? AND workspace_id = ?", vals)
        self.conn.commit()
        return self.get(issue_id)

    def claim(self, issue_id: int, agent_name: str) -> bool:
        cur = self.conn.execute(
            "UPDATE issues SET status = 'analysing', assigned_to = ?, updated_at = ? "
            "WHERE issue_id = ? AND workspace_id = ? AND status = 'open'",
            (agent_name, datetime.utcnow().isoformat(), issue_id, self.wid))
        self.conn.commit()
        return cur.rowcount > 0


class ChatroomRepository:
    """Messages de discussion d'un workspace."""

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id

    def post(self, sender_agent_id: int, content: str,
             msg_type: str = "text", parent_id: int = None) -> Dict[str, Any]:
        cur = self.conn.execute("""
            INSERT INTO chatroom_messages
                (workspace_id, sender_agent_id, msg_type, content, parent_id)
            VALUES (?, ?, ?, ?, ?)
        """, (self.wid, sender_agent_id, msg_type, content, parent_id))
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, msg_id: int) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM chatroom_messages WHERE id = ? AND workspace_id = ?",
            (msg_id, self.wid)).fetchone())

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM chatroom_messages WHERE workspace_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (self.wid, limit)).fetchall())

    def thread(self, root_id: int) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute("""
            WITH RECURSIVE tree AS (
                SELECT * FROM chatroom_messages WHERE id = ?
                UNION ALL
                SELECT m.* FROM chatroom_messages m
                JOIN tree ON m.parent_id = tree.id
            )
            SELECT * FROM tree WHERE workspace_id = ? ORDER BY created_at
        """, (root_id, self.wid)).fetchall())


class UsageFileRepository:
    """Tracking d'accès aux fichiers d'un workspace."""

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id

    def touch_read(self, path: str) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute("""
            INSERT INTO usage_files (path, workspace_id, access_count,
                                     last_read_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(workspace_id, path) DO UPDATE SET
                access_count = access_count + 1,
                last_read_at = ?
        """, (path, self.wid, now, now))
        self.conn.commit()

    def touch_write(self, path: str) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute("""
            INSERT INTO usage_files (path, workspace_id, access_count,
                                     last_write_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(workspace_id, path) DO UPDATE SET
                access_count = access_count + 1,
                last_write_at = ?
        """, (path, self.wid, now, now))
        self.conn.commit()

    def hot(self, limit: int = 10) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute("""
            SELECT * FROM usage_files WHERE workspace_id = ?
            ORDER BY access_count DESC LIMIT ?
        """, (self.wid, limit)).fetchall())

    def get(self, path: str) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM usage_files WHERE workspace_id = ? AND path = ?",
            (self.wid, path)).fetchone())


# ── DB ────────────────────────────────────────────


class WorkspaceDB:
    """Point d'entrée pour workspace.db.

    Chargement paresseux : les requêtes ne lisent que les pages
    SQLite nécessaires. Les listes de workspaces sont légères.

    Usage:
        db = WorkspaceDB()
        for ws in db.workspaces.list():
            print(ws["name"])
        w = db.for_workspace("proj42")
        w.tasks.create("Fix bug")
        for msg in w.chat.recent():
            print(msg["content"])
        db.close()
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_workspace_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit : les agents tournent en threads (agent-manager) et le
        # watcher est un process séparé qui partagent cette DB. Sans autocommit,
        # un write non commité laisse une transaction implicite ouverte → lock
        # d'écriture tenu à vie → "database is locked" pour les autres
        # écrivains. Chaque execute est immédiat.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()
        # (autocommit : plus de transaction implicite à fermer après le DDL)
        self.workspaces = WorkspaceRepository(self.conn)
        self.config = WorkspaceConfigRepository(self.conn)

    def _ensure_schema(self):
        schema = Path(__file__).resolve().parent / "workspace_schema.sql"
        if schema.exists():
            try:
                self.conn.executescript(schema.read_text())
            except Exception:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
        # Migrations : colonnes ajoutées sur des tables déjà existantes.
        try:
            from modules.sql.db import _add_column_if_missing
            _add_column_if_missing(self.conn, "tasks", "difficulty", "TEXT DEFAULT 'medium'")
            _add_column_if_missing(self.conn, "tasks", "role_required", "TEXT DEFAULT ''")
            _add_column_if_missing(self.conn, "tasks", "team_id", "INTEGER DEFAULT -1")
            _add_column_if_missing(self.conn, "issues", "team_id", "INTEGER DEFAULT -1")
            # V0.8.9 : lien issue → workspace d'analyse (le workspace où les
            # tasks de découpage vivent). Permet de marquer l'issue 'done'
            # quand toutes ses tasks sont terminées.
            _add_column_if_missing(self.conn, "issues", "analysis_workspace_id", "TEXT")
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
        try:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_role "
                "ON tasks(workspace_id, role_required, status, priority)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_team "
                "ON tasks(team_id, status, role_required)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_issues_team "
                "ON issues(team_id, status)")
        except Exception:
            pass

    def for_workspace(self, workspace_id: str) -> "WorkspaceScope":
        return WorkspaceScope(self.conn, workspace_id)

    def close(self):
        self.conn.close()


class WorkspaceScope:
    """Accès à toutes les données d'un workspace spécifique.

    Créé par WorkspaceDB.for_workspace().
    Les lectures sont paresseuses (SQLite lit page par page).
    """

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id
        self.tasks = TaskRepository(conn, workspace_id)
        self.issues = IssueRepository(conn, workspace_id)
        self.chat = ChatroomRepository(conn, workspace_id)
        self.usage = UsageFileRepository(conn, workspace_id)
