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
    status       TEXT DEFAULT 'todo',   -- todo | doing
    priority     INTEGER DEFAULT 0,
    assigned_to  TEXT DEFAULT '',
    -- Dépôt + branche ciblés par la tâche (composition : une tâche pointe sur
    -- un repo local + une branche (ou un commit). Vide = repo/HEAD par défaut.
    repo         TEXT DEFAULT '',
    branch       TEXT DEFAULT '',
    commit_hash  TEXT DEFAULT '',
    base_commit  TEXT DEFAULT '',
    -- Type de la tâche = étape du pipeline (coding, code_review, merger_code,
    -- testing_code, analysis, split, …). Un agent pioche les task_type qu'il
    -- sait traiter (liste passée au token_task_pick), avec un niveau max de
    -- difficulté par type.
    task_type    TEXT DEFAULT '',
    -- Difficulté de la tâche (easy / medium / hard / expert). Le niveau de
    -- l'agent (débutant/junior/intermédiaire/senior) borne la difficulté
    -- piochable par type.
    difficulty   TEXT DEFAULT 'medium',
    -- Espace de travail : -1 = projet (partagé), sinon team_id de la team qui
    -- traite la tâche (une team peut travailler sur plusieurs projets).
    team_id      INTEGER DEFAULT -1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Parenté des tâches (dépendances). Une tâche enfant n'est PIOCHABLE que si
-- TOUS ses parents sont dans l'état requis (dépendance totale). Chaque ligne
-- est un lien d'affiliation enfant → parent avec l'état du parent exigé pour
-- débloquer l'enfant (ex. done, merged). Split A → B,C,D : B,C,D ont une ligne
-- (B→A), (C→A), (D→A) ; B,C,D se débloquent quand A passe à l'état requis.
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id        INTEGER NOT NULL,  -- enfant (dépendant)
    parent_id      INTEGER NOT NULL,  -- parent (dont on dépend)
    required_state TEXT DEFAULT 'done',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (task_id, parent_id),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES tasks(task_id) ON DELETE CASCADE
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
    -- Espace de travail : -1 = projet, sinon team_id de la team qui traite.
    team_id      INTEGER DEFAULT -1,
    -- Workspace où vivent les tasks de découpage de cette issue (rempli par
    -- l'analyste) — permet de marquer l'issue 'done' quand ses tasks sont finies.
    analysis_workspace_id TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS task_files (
    task_id INTEGER NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    path    TEXT NOT NULL,
    role    TEXT DEFAULT 'source',
    PRIMARY KEY (task_id, path)
);

-- Choix humain requis : une issue/tâche bloquée en attente d'une décision.
-- L'agent signale via issue_block ; l'humain répond via l'API human_choice/* ;
-- le watcher débloque l'issue quand status='answered'.
CREATE TABLE IF NOT EXISTS human_choice (
    choice_id    TEXT PRIMARY KEY,
    issue_id     INTEGER,
    task_id      INTEGER,
    question     TEXT NOT NULL,
    options_json TEXT,
    status       TEXT DEFAULT 'pending',  -- pending / answered
    response     TEXT,
    asked_at     INTEGER DEFAULT (strftime('%s','now')),
    answered_at  INTEGER
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
