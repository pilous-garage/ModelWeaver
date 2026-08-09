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

# Niveaux de difficulté d'une tâche (croissant). Le niveau de l'agent
# (débutant/junior/intermédiaire/senior) borne la difficulté piochable par
# type : un coder senior peut traiter coding/easy..expert, un reviewer_code
# junior seulement code_review/easy+medium.
_DIFFICULTY_RANK = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}


def _migrate_role_required_to_task_type(conn) -> None:
    """V0.10 : bascule vers le modèle token (task_type + dépendances).

    Les données existantes n'ont pas de valeur (phase de dev) → clean de la
    table tasks UNE FOIS au moment du passage au nouveau schéma (quand
    role_required/parent_id sont encore présents). Après, rien n'est effacé.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    migrated = False
    # Renommer la colonne si elle existe encore et que task_type est absent.
    if "role_required" in cols and "task_type" not in cols:
        try:
            conn.execute("ALTER TABLE tasks RENAME COLUMN role_required TO task_type")
            migrated = True
        except Exception:
            pass
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    # Si role_required coexiste encore avec task_type (migration précédente
    # partielle), on le droppe : task_type est la source de vérité.
    if "role_required" in cols and "task_type" in cols:
        try:
            conn.execute("ALTER TABLE tasks DROP COLUMN role_required")
            migrated = True
        except Exception:
            pass
    # parent_id devient redondant : task_dependencies est la seule source de
    # parenté.
    cols2 = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "parent_id" in cols2:
        try:
            conn.execute("ALTER TABLE tasks DROP COLUMN parent_id")
            migrated = True
        except Exception:
            pass
    # Clean des données UNIQUEMENT à la migration (rien de valeur à préserver
    # en phase de dev). Ne pas ré-effacer à chaque ouverture.
    if migrated:
        try:
            conn.execute("DELETE FROM task_dependencies")
            conn.execute("DELETE FROM tasks")
            conn.commit()
        except Exception:
            pass


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
               priority: int = 0,
               difficulty: str = "medium", task_type: str = "",
               team_id: int = -1, repo: str = "",
               branch: str = "", base_commit: str = "") -> Dict[str, Any]:
        now = datetime.utcnow().isoformat()
        cur = self.conn.execute("""
            INSERT INTO tasks (workspace_id, title, description, priority,
                               status, difficulty, task_type, team_id,
                               repo, branch, base_commit, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'todo', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.wid, title, description, priority,
              difficulty, task_type, team_id, repo, branch, base_commit,
              now, now))
        self.conn.commit()
        return self.get(cur.lastrowid)

    def add_dependency(self, child_id: int, parent_id: int,
                       required_state: str = "done") -> bool:
        """Lie un enfant à un parent : l'enfant n'est piochable que si le
        parent est dans l'état requis (dépendance totale). Idempotent."""
        self.conn.execute("""
            INSERT OR IGNORE INTO task_dependencies
                (task_id, parent_id, required_state)
            VALUES (?, ?, ?)
        """, (child_id, parent_id, required_state))
        self.conn.commit()
        return True

    def get_parents(self, task_id: int) -> List[Dict[str, Any]]:
        """Parents de la tâche (dont elle dépend) avec leur état requis."""
        return _rows(self.conn.execute(
            "SELECT d.parent_id, d.required_state, p.status, p.task_type, p.title "
            "FROM task_dependencies d JOIN tasks p ON p.task_id = d.parent_id "
            "WHERE d.task_id = ?", (task_id,)).fetchall())

    def _parents_blocked_sql(self) -> str:
        """Sous-requête : la tâche t est BLOQUÉE si un de ses parents n'est pas
        dans l'état requis (dépendance totale : TOUS les parents requis)."""
        return ("""
            NOT EXISTS (
                SELECT 1 FROM task_dependencies d
                WHERE d.task_id = t.task_id
                  AND (SELECT status FROM tasks p WHERE p.task_id = d.parent_id)
                      != d.required_state
            )
        """)

    def claim_next(self, task_types: List[str] = (),
                   max_difficulty: Dict[str, str] = None,
                   exclude_assigned: tuple = (),
                   team_id: int = -1,
                   assigned_to: str = "") -> Optional[Dict[str, Any]]:
        """Pioche le prochain token piochable (greedy) pour les task_types donnés.

        Un agent pioche les tokens dont le task_type est dans `task_types` et
        dont la difficulté est ≤ `max_difficulty[task_type]` (le niveau de
        l'agent borne la difficulté piochable par type). Dépendance totale :
        un token n'est piochable que si TOUS ses parents sont dans l'état
        requis (task_dependencies). Statut cible : 'todo' → 'doing'.
        Atomique.
        """
        task_types = [t for t in (task_types or []) if t]
        if not task_types:
            return None
        max_diff = max_difficulty or {}
        sel_args = [self.wid]
        ph = ",".join("?" for _ in task_types)
        sel = ("SELECT t.* FROM tasks t WHERE t.workspace_id = ? "
               "AND t.status = 'todo' AND t.task_type IN (" + ph + ")")
        sel_args.extend(task_types)
        # Difficulté maximale piochable par type (le niveau de l'agent).
        diff_conds = []
        for tt in task_types:
            mx = max_diff.get(tt)
            if mx and mx in _DIFFICULTY_RANK:
                diff_conds.append(
                    f"(t.task_type = ? AND {_DIFFICULTY_RANK[mx]} >= "
                    f"CASE t.difficulty WHEN 'easy' THEN 0 WHEN 'medium' THEN 1 "
                    f"WHEN 'hard' THEN 2 WHEN 'expert' THEN 3 ELSE 1 END)")
                sel_args.append(tt)
        if diff_conds:
            sel += " AND (" + " OR ".join(diff_conds) + ")"
        # team_id : -1 (projet) OU la team de l'agent
        sel += " AND (t.team_id = ? OR t.team_id = -1)"
        sel_args.append(team_id)
        # Dépendance totale : tous les parents à l'état requis.
        sel += " AND " + self._parents_blocked_sql()
        if exclude_assigned:
            ph2 = ",".join("?" for _ in exclude_assigned)
            sel += f" AND COALESCE(t.assigned_to,'') NOT IN ({ph2})"
            sel_args.extend(exclude_assigned)
        # Prioriser les tâches de la TEAM de l'agent avant le projet partagé.
        sel += (" ORDER BY CASE WHEN t.team_id = ? THEN 0 ELSE 1 END, "
                "t.priority DESC, t.created_at LIMIT 1")
        sel_args.append(team_id)
        row = _row(self.conn.execute(sel, sel_args).fetchone())
        if not row:
            return None
        cur = self.conn.execute(
            "UPDATE tasks SET status = 'doing', assigned_to = ?, updated_at = ? "
            "WHERE task_id = ? AND workspace_id = ? AND status = 'todo'",
            (assigned_to, datetime.utcnow().isoformat(),
             row["task_id"], self.wid))
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

    def set_status(self, task_id: int, status: str,
                   branch: str = "", commit_hash: str = "",
                   assigned_to: str = "") -> Optional[Dict[str, Any]]:
        """Passe un token à un statut arbitraire (todo/doing/done/merged...)."""
        sets = ["status = ?", "updated_at = ?"]
        vals = [status, datetime.utcnow().isoformat()]
        if branch:
            sets.append("branch = ?")
            vals.append(branch)
        if commit_hash:
            sets.append("commit_hash = ?")
            vals.append(commit_hash)
        if assigned_to:
            sets.append("assigned_to = ?")
            vals.append(assigned_to)
        vals.extend([task_id, self.wid])
        self.conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} "
            "WHERE task_id = ? AND workspace_id = ?", vals)
        self.conn.commit()
        return self.get(task_id)

    def modify(self, task_id: int, new_task_type: str = None,
               status: str = None, branch: str = "",
               commit_hash: str = "", assigned_to: str = None,
               clear_assigned: bool = False) -> Optional[Dict[str, Any]]:
        """Transition de token vers l'étape suivante du pipeline.

        Conceptuellement DÉTRUIT le token courant et CRÉE le token suivant
        (même task_id : on change task_type + status). Ex. fin d'un coding :
        modify(task, new_task_type='code_review', status='todo') → le token
        devient un code_review piochable par les reviewers.

        `assigned_to=None` : ne touche pas l'assignation. `clear_assigned=True` :
        libère le token (fin de traitement → nouveau token à piocher).
        """
        sets = []
        vals = []
        if new_task_type is not None:
            sets.append("task_type = ?")
            vals.append(new_task_type)
        if status is not None:
            sets.append("status = ?")
            vals.append(status)
        if branch:
            sets.append("branch = ?")
            vals.append(branch)
        if commit_hash:
            sets.append("commit_hash = ?")
            vals.append(commit_hash)
        if clear_assigned:
            sets.append("assigned_to = ''")
        elif assigned_to is not None:
            sets.append("assigned_to = ?")
            vals.append(assigned_to)
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
            _add_column_if_missing(self.conn, "tasks", "team_id", "INTEGER DEFAULT -1")
            # V0.9.x : une tâche pointe sur un repo local + branche (ou commit).
            _add_column_if_missing(self.conn, "tasks", "repo", "TEXT DEFAULT ''")
            _add_column_if_missing(self.conn, "tasks", "base_commit", "TEXT DEFAULT ''")
            # V0.10 : rôle requis → type de tâche (étape du pipeline). Une
            # tâche 'coder_senior' devient task_type 'coding' (le niveau de
            # l'agent borne la difficulté piochable). Clean des données (rien
            # de valeur à préserver en phase de dev).
            _migrate_role_required_to_task_type(self.conn)
            # Parenté des tâches (dépendance totale : tous les parents à l'état
            # requis pour débloquer l'enfant).
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS task_dependencies (
                    task_id        INTEGER NOT NULL,
                    parent_id      INTEGER NOT NULL,
                    required_state TEXT DEFAULT 'done',
                    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (task_id, parent_id),
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                    FOREIGN KEY (parent_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_deps_child "
                "ON task_dependencies(task_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_deps_parent "
                "ON task_dependencies(parent_id)")
            _add_column_if_missing(self.conn, "issues", "team_id", "INTEGER DEFAULT -1")
            # V0.8.9 : lien issue → workspace d'analyse (le workspace où les
            # tasks de découpage vivent). Permet de marquer l'issue 'done'
            # quand toutes ses tasks sont terminées.
            _add_column_if_missing(self.conn, "issues", "analysis_workspace_id", "TEXT")
            # V0.8.9 : human_choice — choix humain requis (issue/task bloquée
            # en attente d'une réponse). L'agent signale via issue_block ; le
            # watcher débloque quand l'humain répond via l'API human_choice/*.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS human_choice (
                    choice_id    TEXT PRIMARY KEY,
                    issue_id     INTEGER,
                    task_id      INTEGER,
                    question     TEXT NOT NULL,
                    options_json TEXT,
                    status       TEXT DEFAULT 'pending',
                    response     TEXT,
                    asked_at     INTEGER DEFAULT (strftime('%s','now')),
                    answered_at  INTEGER
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_human_choice_status "
                "ON human_choice(status)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_human_choice_issue "
                "ON human_choice(issue_id)")
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
