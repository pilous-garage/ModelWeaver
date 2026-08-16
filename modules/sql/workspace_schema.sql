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
    -- Tracking git du cycle de vie : commit_start/branch_start = point de
    -- départ (créés avec la tâche, ou déduits par le 1er picker) ;
    -- commit_current/branch_current = position git courante (mise à jour à
    -- chaque étape du pipeline). Permet le cancel (reset au commit_start) et
    -- le clear (vérifier le travail livré).
    commit_start  TEXT DEFAULT '',
    branch_start  TEXT DEFAULT '',
    commit_current TEXT DEFAULT '',
    branch_current TEXT DEFAULT '',
    -- Primordiale : tâche créée directement par le chat/une issue (racine).
    -- Secondaire : tout split/découpe par les agents. clear/cancel ne s'étend
    -- JAMAIS au-delà des secondaires (un parent dans un autre groupe survive).
    primordial   INTEGER DEFAULT 0,
    -- Annulée : flag séparé (pas un statut). Une tâche cancelled peut rester
    -- done (canceled done) — on ne supprime pas le code, on protège sur une
    -- branche canceled_<id> et on reset au commit_start.
    cancelled    INTEGER DEFAULT 0,
    -- Ordonnancement : échéance (deadline) et durée estimée. La priorité de
    -- pioche est un score = base_priority + W_DEADLINE*urgence (temps estimé
    -- long + deadline courte → urgent ; en retard → max) + W_AGE*âge.
    deadline         TEXT DEFAULT '',
    estimated_minutes INTEGER DEFAULT 0,
    -- Rotation des agents : le dernier agent qui a tenté la tâche et a échoué
    -- (token_task_release le pose). Permet à l'ordonnanceur d'exclure/pénaliser
    -- cet agent et de laisser un autre membre la reprendre.
    freedby          TEXT DEFAULT '',
    -- Type de la tâche = étape du pipeline (coding, code_review, merger_code,
    -- testing_code, analysis, split, …). Un agent pioche les task_type qu'il
    -- sait traiter (liste passée au token_task_pick), avec un niveau max de
    -- difficulté par type.
    task_type    TEXT DEFAULT '',
    -- DOMAINE de la requête (classification) : text_generation | coding |
    -- math | data | reasoning | research | admin… Le type = route/pipeline,
    -- le domaine = nature de la requête (utile pour le découpage, le choix
    -- des agents, la réponse directe).
    domain       TEXT DEFAULT '',
    -- Difficulté de la tâche (easy / medium / hard / expert). Le niveau de
    -- l'agent (débutant/junior/intermédiaire/senior) borne la difficulté
    -- piochable par type.
    difficulty   TEXT DEFAULT 'medium',
    -- Espace de travail : -1 = projet (partagé), sinon team_id de la team qui
    -- traite la tâche (une team peut travailler sur plusieurs projets).
    team_id      INTEGER DEFAULT -1,
    -- Tag du SUJET (terminal, posé par le supervisor) : résultat qualifié du
    -- pipeline complet (ex. done/ok, done/failure…). Champs du nouveau
    -- taskflow (V0.15) : le workflow vit dans sub_tasks, tasks = le sujet.
    tag          TEXT DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Sub_tasks : le RELAIS (une ligne par étape du pipeline) ─────────────
-- V0.15 — taskflow : le workflow est porté par la création de nouvelles
-- sub_tasks + dépendances, PAS par des mutations de type. Chaque étape
-- (analysis, coding, testing, review, merge, respond…) est une ligne.
-- États : waiting_dependencies → unattributed → doing → done/cancelled → supervised.
CREATE TABLE IF NOT EXISTS sub_tasks (
    sub_task_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id) ON DELETE CASCADE,
    task_id      INTEGER NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    team_id      INTEGER DEFAULT -1,
    sub_task_type TEXT NOT NULL,          -- analysis | coding | testing | review | merge | respond
    status       TEXT DEFAULT 'unattributed',
    tag          TEXT DEFAULT '',          -- posé par l'agent d'exécution, contraint par type
    difficulty   TEXT DEFAULT 'medium',
    description  TEXT DEFAULT '',          -- consigne/description de l'étape (ex. intels de l'exploration)
    assigned_to  TEXT DEFAULT '',
    freedby      TEXT DEFAULT '',
    priority     INTEGER DEFAULT 0,        -- score de pioche (le supervisor met à jour)
    too_hard_count INTEGER DEFAULT 0,      -- nb d'abandons (limite de boucle too_hard)
    too_hard_reason TEXT DEFAULT '',       -- dernière raison d'abandon
    supervised   INTEGER DEFAULT 0,        -- groupe complet clos par le supervisor
    repo         TEXT DEFAULT '',
    branch       TEXT DEFAULT '',
    commit_hash  TEXT DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_subtasks_team_status
    ON sub_tasks(team_id, status, supervised, sub_task_type);
CREATE INDEX IF NOT EXISTS idx_subtasks_assigned
    ON sub_tasks(assigned_to, status);
CREATE INDEX IF NOT EXISTS idx_subtasks_assigned_priority
    ON sub_tasks(assigned_to, status, priority);
CREATE INDEX IF NOT EXISTS idx_subtasks_task
    ON sub_tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_subtasks_workspace_status
    ON sub_tasks(workspace_id, status, supervised);

-- ── Dépendances entre sub_tasks (relais) ────────────────────────────────
-- V0.15 — une sub_task enfant attend que ses parents (sub_tasks) soient à
-- l'état + tag requis (ex. parent coding en done/ok). Le supervisor lève le
-- waiting_dependencies quand toutes les lignes sont satisfaites.
CREATE TABLE IF NOT EXISTS sub_task_dependencies (
    child_id       INTEGER NOT NULL,
    parent_id      INTEGER NOT NULL,
    required_state TEXT DEFAULT 'done',
    required_tag   TEXT DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (child_id, parent_id),
    FOREIGN KEY (child_id)  REFERENCES sub_tasks(sub_task_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES sub_tasks(sub_task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sub_task_deps_child ON sub_task_dependencies(child_id);
CREATE INDEX IF NOT EXISTS idx_sub_task_deps_parent ON sub_task_dependencies(parent_id);

-- ── Ask_new_task : file d'attribution (greedy) ───────────────────────────-- V0.15 — l'agent greedy demande du travail au supervisor (remplace
-- sleep + pick). La demande est écrite en BDD (traçabilité + file de secours
-- pour le tick failsafe), puis le supervisor répond en synchrone.
CREATE TABLE IF NOT EXISTS ask_new_task (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL,
    agent_id     INTEGER NOT NULL,
    types        TEXT DEFAULT '[]',        -- JSON: [{type, level_max}]
    status       TEXT DEFAULT 'pending',   -- pending | served | answered_wait
    requested_at TEXT NOT NULL DEFAULT (datetime('now')),
    served_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ask_new_task_status ON ask_new_task(status, workspace_id);

-- ── Task_supervisor_rules : tableau de règles par team ───────────────────
-- V0.15 — (type_entrant, tag_entrant) → (type_sortant, tag_sortant).
-- Le supervisor est généraliste par défaut ; chaque team PEUT surcharger
-- (le champ est obligatoire dans la déclaration d'une team).
CREATE TABLE IF NOT EXISTS task_supervisor_rules (
    rule_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT DEFAULT '',
    team_id      INTEGER DEFAULT -1,
    in_type      TEXT NOT NULL,            -- sub_task_type entrant ('' = tous)
    in_tag       TEXT DEFAULT '',          -- tag entrant ('' = tous)
    out_type     TEXT NOT NULL,            -- sub_task_type créé ('' = none → supervised)
    out_tag      TEXT DEFAULT '',          -- tag posé sur la nouvelle sub_task
    priority     INTEGER DEFAULT 0,        -- règle la plus spécifique gagne
    enabled      INTEGER DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_supervisor_rules
    ON task_supervisor_rules(workspace_id, team_id, in_type, in_tag);

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

-- ── Consensus : question + réponses des answering_machine ────────────────
-- L'agent consensus (maître) pose une question ; 5 answering_machine (sub-
-- agents, modèles DIFFÉRENTS via ask_llm not_same_modele) y répondent ;
-- le maître juge et détermine le consensus (vote majorité, élimination,
-- escalade, hasard). Voir docs/carnet-d-idees.md (Idée 17, Système A).
CREATE TABLE IF NOT EXISTS question (
    id_question     INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    TEXT NOT NULL DEFAULT '',
    question        TEXT NOT NULL,
    id_creator      INTEGER,                 -- agent maître qui pose
    status_answering TEXT DEFAULT 'awaiting', -- awaiting / answered / cancelled
    options_json    TEXT,                    -- A/B/C/... (le cas échéant)
    max_tours       INTEGER DEFAULT 5,       -- tours NEW autorisés
    tour_courant    INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now')),
    answered_at     TEXT
);

CREATE TABLE IF NOT EXISTS reponse (
    id_reponse      INTEGER PRIMARY KEY AUTOINCREMENT,
    id_question     INTEGER NOT NULL REFERENCES question(id_question) ON DELETE CASCADE,
    id_agent        INTEGER NOT NULL,        -- answering_machine
    model_ref       TEXT DEFAULT '',         -- modèle qui a répondu
    contenu         TEXT NOT NULL,           -- texte clair (commit vide)
    jugement        TEXT DEFAULT '',         -- vote A/B/C / NEW / note
    similar_to      INTEGER,                 -- ≈ autre reponse (non tranché)
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reponse_question ON reponse(id_question);

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
