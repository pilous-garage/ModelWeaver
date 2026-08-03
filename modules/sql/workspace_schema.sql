-- ──────────────────────────────────────────
--  Workspace DB — projets, tâches, échanges
-- ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id    TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    director        TEXT DEFAULT NULL,
    git_shared      TEXT DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_activity_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workspace_config (
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,
    PRIMARY KEY (workspace_id, key)
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    status       TEXT DEFAULT 'pending',
    priority     INTEGER DEFAULT 0,
    assigned_to  TEXT DEFAULT '',
    branch       TEXT DEFAULT '',
    commit_hash  TEXT DEFAULT '',
    parent_id    INTEGER,
    -- Swarm glouton : difficulté de la tâche et rôle requis pour la traiter.
    difficulty   TEXT DEFAULT 'medium',   -- easy / medium / hard / expert
    role_required TEXT DEFAULT '',        -- analyst / coder_junior / coder_senior / tester / reviewer / merger
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Issues : demandes de haut niveau analysées par l'analyste puis découpées
-- en tâches (tasks.parent_id = issue issue_id). Le manager choisit une issue
-- et la pose ici ; l'analyste la traite (statut analysée → tâches générées).
CREATE TABLE IF NOT EXISTS issues (
    issue_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    status       TEXT DEFAULT 'open',     -- open / analysing / analyzed / in_progress / closed
    priority     INTEGER DEFAULT 0,
    assigned_to  TEXT DEFAULT '',
    parent_id    INTEGER,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_files (
    task_id INTEGER NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    path    TEXT NOT NULL,
    role    TEXT DEFAULT 'source',
    PRIMARY KEY (task_id, path)
);

CREATE TABLE IF NOT EXISTS chatroom_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    sender_agent_id INTEGER,
    msg_type        TEXT DEFAULT 'text',
    content         TEXT NOT NULL,
    parent_id       INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (parent_id) REFERENCES chatroom_messages(id)
);

CREATE TABLE IF NOT EXISTS usage_files (
    path          TEXT NOT NULL,
    workspace_id  TEXT NOT NULL,
    access_count  INTEGER DEFAULT 0,
    last_read_at  TEXT,
    last_write_at TEXT,
    PRIMARY KEY (workspace_id, path)
);

-- Index pour les requêtes workspace
CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(workspace_id, status, priority);
CREATE INDEX IF NOT EXISTS idx_issues_workspace ON issues(workspace_id, status, priority);
CREATE INDEX IF NOT EXISTS idx_chatroom_workspace ON chatroom_messages(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS idx_usage_files_workspace ON usage_files(workspace_id, access_count DESC);
