-- ============================================================
-- TASK.DB — tâches, sub_tasks, dépendances, ask_new_task (domaine task).
-- Refonte planifiée : fusion tasks + sub_tasks → 1 table auto-référentielle
-- tasks(parent_task_id) + entry_request (racine immutable) +
-- task_dependencies(child,parent,required_state,required_tag) unifiée +
-- ask_new_task (file greedy). Spec : docs/local_catalogue_spec.md §4.1 +
-- .opencode/last_session.md.
--
-- PRINCIPE :
--   - entry_request : racine immuable d'une conversation (root_id jamais
--     réutilisé) ; tasks.entry_request_id la porte toujours.
--   - tasks : un arbre — issue-level (parent_task_id NULL) ou sub_task
--     (issue d'un split/merge), sub_task_type renseigné.
--   - task_log / task_budget_tracking agrégent par entry_request_id (scoreur).
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS task_meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO task_meta(key, value) VALUES ('schema_version', 1);

-- 1. ENTRY_REQUEST — racine immutable de la conversation (root_tasks)
CREATE TABLE IF NOT EXISTS entry_request (
    entry_request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id     TEXT NOT NULL DEFAULT '',
    prompt_hash      TEXT NOT NULL DEFAULT '',
    title            TEXT DEFAULT '',
    asked_at         TEXT DEFAULT (datetime('now')),
    UNIQUE(workspace_id, prompt_hash)
);
CREATE INDEX IF NOT EXISTS idx_er_workspace ON entry_request(workspace_id, asked_at DESC);

-- 2. TASKS — table unifiée (tasks + sub_tasks), arbre auto-référentiel
--    sub_task_type NULL = sujet issue-level ; sinon = analysis|coding|...
CREATE TABLE IF NOT EXISTS tasks (
    task_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id      TEXT NOT NULL DEFAULT '',
    entry_request_id  INTEGER NOT NULL REFERENCES entry_request(entry_request_id)
                           ON DELETE CASCADE,
    parent_task_id    INTEGER REFERENCES tasks(task_id) ON DELETE CASCADE,
    sub_task_type     TEXT, -- analysis|coding|testing|review|merge|respond
    status            TEXT NOT NULL DEFAULT 'todo'
                      CHECK(status IN ('todo','waiting_deps','attributed',
                                      'doing','done','too_hard','error',
                                      'cancelled')),
    tag               TEXT DEFAULT '', -- sujet terminal posé par le supervisor
    priority          INTEGER DEFAULT 0,
    difficulty        TEXT DEFAULT 'medium',
    task_type         TEXT DEFAULT '', -- text_generation|coding|math|...
    domain            TEXT DEFAULT '',
    assigned_to       TEXT DEFAULT '',
    freedby           TEXT DEFAULT '',
    too_hard_count    INTEGER DEFAULT 0,
    too_hard_reason   TEXT DEFAULT '',
    supervised        INTEGER DEFAULT 0,
    repo              TEXT DEFAULT '',
    branch            TEXT DEFAULT '',
    commit_hash       TEXT DEFAULT '',
    commit_start      TEXT DEFAULT '',
    branch_start      TEXT DEFAULT '',
    commit_current    TEXT DEFAULT '',
    branch_current    TEXT DEFAULT '',
    deadline          TEXT DEFAULT '',
    estimated_minutes INTEGER DEFAULT 0,
    team_id           INTEGER DEFAULT -1,
    title             TEXT DEFAULT '',
    description       TEXT DEFAULT '',
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_workspace_status
    ON tasks(workspace_id, status, priority);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned
    ON tasks(assigned_to, status, priority);
CREATE INDEX IF NOT EXISTS idx_tasks_team_status
    ON tasks(team_id, status, supervised, sub_task_type);
CREATE INDEX IF NOT EXISTS idx_tasks_entry
    ON tasks(entry_request_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent
    ON tasks(parent_task_id);

-- 3. TASK_DEPENDENCIES — unification sub_task_dependencies + task_dependencies
CREATE TABLE IF NOT EXISTS task_dependencies (
    child_id      INTEGER NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    parent_id     INTEGER NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    required_state TEXT NOT NULL DEFAULT 'done',
    required_tag   TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (child_id, parent_id)
);
CREATE INDEX IF NOT EXISTS idx_td_child ON task_dependencies(child_id);
CREATE INDEX IF NOT EXISTS idx_td_parent ON task_dependencies(parent_id);

-- 4. ASK_NEW_TASK — file greedy (agent idle → demande tâche → supervisor sert)
CREATE TABLE IF NOT EXISTS ask_new_task (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    agent_id     INTEGER NOT NULL,
    types        TEXT NOT NULL DEFAULT '[]',   -- JSON [{type, level_max}]
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK(status IN ('pending','served','answered_wait')),
    requested_at TEXT NOT NULL DEFAULT (datetime('now')),
    served_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ask_new_task_status ON ask_new_task(status, workspace_id);

-- Annexes de tâche hors git (fusion workspace task_reports + task_files) :
-- infos textuelles (rapports analysis/exploration/too_hard…) et fichiers
-- attachés à la tâche. kind = 'text' (content) / 'file' (path, unique par
-- (task_id, path) → INSERT OR IGNORE idempotent). Le livrable git vit dans
-- branch/commit_hash des sub_tasks (tasks) — pas ici.
CREATE TABLE IF NOT EXISTS task_attachments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'text' CHECK(kind IN ('text', 'file')),
    role       TEXT NOT NULL DEFAULT 'work',
    content    TEXT DEFAULT '',
    path       TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_task_attachments_task ON task_attachments(task_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_attachments_file
    ON task_attachments(task_id, path) WHERE kind = 'file';

-- Data-type project : (source, project_ref) identifie UN projet de façon
-- unique. `source` distingue les projets de même nom venant de sources
-- variées (ex. github.com/user/repo vs un local) — mêmes conventions que
-- les autres domaines (buffer/info_llm : source par réf). `tasks.repo` /
-- `sub_tasks.repo` portent project_ref ; le registre project donne le
-- (source, project_ref) qui désambiguïse.
-- user_dir = dossier PROJET UTILISATEUR (la VRAIE version des files, hors
-- des clones agents) : l'init/maj/discussion entre cette vue utilisateur et
-- le repo local géré par les agents s'appuie sur ce chemin.
CREATE TABLE IF NOT EXISTS project (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL DEFAULT 'local',
    project_ref TEXT NOT NULL,
    user_dir    TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, project_ref)
);

-- Registre des repos locaux (un par projet). Modèle git :
--   bare central  : mw_home()/repos/{project_ref}.git   (source de vérité)
--   clone par agent : mw_home()/agent_home/{aid}/workspace/{project_ref}
-- Les refs repo/commit des tasks s'appuient sur ce registre (invariant :
-- repo présent dans une task ⇒ (source, project_ref) présent ici).
-- L'état de synchronisation avec un éventuel distant est conservé :
-- create_local_git_repo_from_distant(adresse, branche, commit) remplit
-- remote_url + last_pull_from/last_pull_commit au point de départ.
CREATE TABLE IF NOT EXISTS local_git_repo (
    repo_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source           TEXT NOT NULL DEFAULT 'local',
    project_ref      TEXT NOT NULL,
    repo_path        TEXT NOT NULL,
    remote_url       TEXT DEFAULT '',
    default_branch   TEXT DEFAULT 'main',
    last_pull        TEXT,                   -- dernière fois pull (datetime UTC)
    last_pull_from   TEXT DEFAULT '',        -- branche/ref distante d'origine
    last_pull_commit TEXT DEFAULT '',        -- commit d'origine du pull
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, project_ref)
);

INSERT OR IGNORE INTO task_meta(key, value) VALUES ('schema_version', 1);
