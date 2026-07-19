-- ModelWeaver Local Database Schema
-- Fichier: .modelweaver/modelweaver.db
-- Contient l'état local : providers, clés, modèles, outils installés.
-- La partie outil est calquée sur la même structure que le catalogue
-- (outils / versions / recettes) pour permettre plusieurs versions
-- d'un même outil installées par des managers différents.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================
-- 1. PROVIDERS — Fournisseurs d'API (copie locale)
-- ============================================================
CREATE TABLE IF NOT EXISTS providers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    provider_type   TEXT NOT NULL CHECK(provider_type IN ('cloud', 'local', 'ollama', 'builtin')),
    api_type        TEXT,
    website         TEXT,
    limits_json     TEXT,
    rate_limits_json TEXT,
    next_reset_at   INTEGER,
    is_free_tier_provider INTEGER DEFAULT 0,
    catalogue_ref   TEXT,
    created_at      INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 2. MODELS — Modèles indépendants des fournisseurs
-- ============================================================
CREATE TABLE IF NOT EXISTS models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    developer       TEXT,
    release_year    INTEGER,
    architecture    TEXT,
    parameter_count TEXT,
    modality        TEXT,
    target_use      TEXT,
    license         TEXT,
    is_open_weights INTEGER DEFAULT 0,
    parent_model_id INTEGER REFERENCES models(id),
    catalogue_ref   TEXT,
    metadata_json   TEXT,
    created_at      INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 3. PROVIDER_MODELS — Lien fournisseur → modèle
-- ============================================================
CREATE TABLE IF NOT EXISTS provider_models (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id         INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    model_id            INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    provider_model_name TEXT NOT NULL,
    context_window_tokens INTEGER,
    max_output_tokens   INTEGER,
    cost_per_input_token  TEXT,
    cost_per_output_token TEXT,
    cost_billing        TEXT,
    pricing_rules_json  TEXT,
    limits_json         TEXT,
    rate_limits_json    TEXT,
    next_reset_at       INTEGER,
    status              TEXT DEFAULT 'active' CHECK(status IN ('active', 'deprecated', 'experimental')),
    metadata_json       TEXT,
    created_at          INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at          INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(provider_id, model_id)
);

-- ============================================================
-- 4. API_KEYS — Clés API stockées (en clair pour l'instant)
-- ============================================================
CREATE TABLE IF NOT EXISTS api_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT UNIQUE NOT NULL,
    identity        TEXT DEFAULT 'default',
    provider_id     INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    key_value       TEXT NOT NULL,
    key_display     TEXT,
    locked          INTEGER DEFAULT 0,
    tag             TEXT NOT NULL CHECK(tag IN ('free', 'paid')),
    grade           TEXT,
    health_status   TEXT DEFAULT 'unknown' CHECK(health_status IN ('unknown', 'ok', 'degraded', 'suspicious', 'dead', 'user_disabled')),
    expiration_date INTEGER,
    last_tested_at  INTEGER,
    last_error      TEXT,
    error_count     INTEGER DEFAULT 0,
    metadata_json   TEXT,
    created_at      INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 5. PACKAGE_MANAGERS — Gestionnaires de paquets OS détectés
-- ============================================================
CREATE TABLE IF NOT EXISTS package_managers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    detected        INTEGER DEFAULT 0,
    version         TEXT,
    install_cmd     TEXT,
    os_family       TEXT DEFAULT 'linux',
    created_at      INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

INSERT OR IGNORE INTO package_managers (ref, name, install_cmd, os_family) VALUES
    ('apt', 'APT', 'apt-get install -y', 'linux'),
    ('snap', 'Snap', 'snap install', 'linux'),
    ('brew', 'Homebrew', 'brew install', 'linux'),
    ('pacman', 'Pacman', 'pacman -S --noconfirm', 'linux'),
    ('yay', 'Yay', 'yay -S --noconfirm', 'linux'),
    ('dnf', 'DNF', 'dnf install -y', 'linux'),
    ('yum', 'YUM', 'yum install -y', 'linux'),
    ('zypper', 'Zypper', 'zypper install -n', 'linux'),
    ('apk', 'Alpine APK', 'apk add', 'linux'),
    ('emerge', 'Gentoo Emerge', 'emerge', 'linux'),
    ('nix', 'Nix', 'nix-env -iA', 'linux'),
    ('flatpak', 'Flatpak', 'flatpak install', 'linux'),
    ('pip', 'Pip', 'pip install', 'linux'),
    ('cargo', 'Cargo', 'cargo install', 'linux'),
    ('npm', 'NPM', 'npm install -g', 'linux'),
    ('go', 'Go', 'go install', 'linux'),
    ('winget', 'WinGet', 'winget install', 'windows'),
    ('choco', 'Chocolatey', 'choco install -y', 'windows');

-- ============================================================
-- 5b. CLASSES_OUTILS — Taxonomie métier des outils (miroir local
--     de la table catalogue.classes_outils). Même seed pour
--     permettre la jointure locale_outils → classe sans accès
--     au catalogue distant.
-- ============================================================
CREATE TABLE IF NOT EXISTS classes_outils (
    classe_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT UNIQUE NOT NULL,
    nom         TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER DEFAULT 0,
    created_at  INTEGER DEFAULT (strftime('%s', 'now'))
);

INSERT OR IGNORE INTO classes_outils (ref, nom, description, sort_order) VALUES
    ('language',  'Languages',       'Interpréteurs et compilateurs',           10),
    ('dev-tool',  'Dev Tools',       'Outils de développement',                 20),
    ('ide',       'IDEs',            'Environnements de développement intégrés', 30),
    ('chat-llm',  'Chat LLM',        'Interfaces de chat avec les LLM',         40),
    ('agent',     'Agents',          'Orchestrateurs IA autonomes',             50),
    ('engine',    'LLM Engines',     'Moteurs d''exécution locale de LLM',      60),
    ('router',    'Routers',         'Passerelles et proxy LLM',                70),
    ('context',   'Context Tools',   'Gestion du contexte et secrets',          80),
    ('system',    'System Tools',    'Utilitaires système',                     90),
    ('other',     'Other',           'Autres outils',                           999);

-- ============================================================
-- 6. LOCAL_OUTILS — Miroir local du catalogue_outils
--    Une ligne par outil connu localement (peut précéder ou non le catalogue).
--    classe_outil_id pointe vers classes_outils (nullable avant migration).
-- ============================================================
CREATE TABLE IF NOT EXISTS local_outils (
    local_outil_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    outil_ref       TEXT UNIQUE NOT NULL,
    nom             TEXT NOT NULL,
    tool_type       TEXT CHECK(tool_type IN ('binary','python-module','archive','source','container')),
    classe_outil_id INTEGER REFERENCES classes_outils(classe_id) ON DELETE SET NULL,
    created_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 7. LOCAL_VERSIONS — Version d'un outil local
-- ============================================================
CREATE TABLE IF NOT EXISTS local_versions (
    local_version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_outil_id   INTEGER NOT NULL REFERENCES local_outils(local_outil_id) ON DELETE CASCADE,
    nom_version      TEXT NOT NULL,
    created_at       INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(local_outil_id, nom_version)
);

-- ============================================================
-- 8. LOCAL_INSTALLS — Instance installée d'un outil
--    Plusieurs lignes possibles pour un même outil/version
--    (ex: litellm installé via pip ET via conda).
--    status = installed / failed / outdated / uninstalled
-- ============================================================
CREATE TABLE IF NOT EXISTS local_installs (
    install_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    local_version_id INTEGER NOT NULL REFERENCES local_versions(local_version_id) ON DELETE CASCADE,
    os               TEXT NOT NULL,
    arch             TEXT NOT NULL,
    manager          TEXT,               -- pip, apt, binary, docker…
    package          TEXT,               -- nom du paquet chez le manager
    version_installee TEXT,
    install_path     TEXT,
    status           TEXT DEFAULT 'pending' CHECK(status IN ('pending','installed','failed','outdated','uninstalled')),
    ts               INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 9. COMMANDS — Utilitaires non triviaux pour les IA
-- ============================================================
CREATE TABLE IF NOT EXISTS commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    command_type TEXT NOT NULL CHECK(command_type IN ('system', 'binary', 'project', 'utility')),
    catalogue_ref TEXT,
    created_at  INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 10. LOCAL_LLMS — LLM téléchargés localement (Ollama, etc.)
-- ============================================================
CREATE TABLE IF NOT EXISTS local_llms (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    model_id    INTEGER REFERENCES models(id),
    ram_required_mb INTEGER,
    chipset     TEXT,
    launch_command TEXT,
    api_base_url TEXT,
    context_window_tokens INTEGER,
    capabilities_json TEXT,
    status      TEXT DEFAULT 'not_downloaded' CHECK(status IN ('not_downloaded', 'downloaded', 'running', 'error')),
    parameters_json TEXT,
    installed_at INTEGER,
    created_at  INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at  INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 11. MODEL_PROVIDERS — Ressources hardware/cloud pour les agents
-- ============================================================
CREATE TABLE IF NOT EXISTS model_providers (
    provider_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    engine_type      TEXT NOT NULL CHECK(engine_type IN ('openai_api', 'litellm', 'ollama', 'llamacpp', 'onnx_runtime', 'transformers')),
    model_name       TEXT NOT NULL,
    endpoint_url     TEXT,
    max_concurrent   INTEGER DEFAULT 1,
    current_concurrent INTEGER DEFAULT 0,
    cooldown_until   TEXT,
    api_key_ref      TEXT REFERENCES api_keys(ref),
    created_at       TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 12. AGENTS — Identité et couplage modèle/hardware
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
    agent_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    role_type     TEXT NOT NULL,
    provider_id   INTEGER REFERENCES model_providers(provider_id),
    status        TEXT DEFAULT 'IDLE' CHECK(status IN ('IDLE', 'BUSY', 'PAUSED', 'STOPPED', 'TERMINATED')),
    config_json   TEXT,
    state_json    TEXT,
    successor_id  INTEGER REFERENCES agents(agent_id),
    created_at    TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 13. SESSIONS — Fils de discussion persistants
-- ============================================================
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    agent_id        INTEGER NOT NULL REFERENCES agents(agent_id),
    status          TEXT DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'COMPLETED', 'ARCHIVED')),
    context_summary TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 14. AGENT_MESSAGES — Mémoire brute au format OpenAI
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_messages (
    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    role       TEXT NOT NULL CHECK(role IN ('system', 'user', 'assistant', 'tool')),
    content    TEXT NOT NULL,
    tokens_used INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 15. WAKEUP_CALLS — Système nerveux (tâches et réveils)
-- ============================================================
CREATE TABLE IF NOT EXISTS wakeup_calls (
    task_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id       INTEGER NOT NULL REFERENCES agents(agent_id),
    session_id     TEXT NOT NULL REFERENCES sessions(session_id),
    skill          TEXT NOT NULL,
    request_payload TEXT,
    status         TEXT DEFAULT 'TODO' CHECK(status IN ('TODO', 'BUSY', 'COMPLETED', 'FAILED')),
    execute_after  TEXT NOT NULL,
    result_summary TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 16. AGENT_QUEUE — Messagerie inter-agents (direct + broadcast)
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_queue (
    queue_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent_id INTEGER NOT NULL REFERENCES agents(agent_id),
    to_agent_id   INTEGER REFERENCES agents(agent_id),   -- NULL = broadcast
    topic         TEXT,                                    -- pour pub/sub, NULL = direct
    message_type  TEXT DEFAULT 'text' CHECK(message_type IN ('text', 'task_assignment', 'notification', 'broadcast')),
    content       TEXT NOT NULL,
    status        TEXT DEFAULT 'TODO' CHECK(status IN ('TODO', 'READ', 'ARCHIVED')),
    created_at    TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 17. CHATROOM_MESSAGES — Board public avec threads
-- ============================================================
CREATE TABLE IF NOT EXISTS chatroom_messages (
    message_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id     INTEGER REFERENCES chatroom_messages(message_id),  -- NULL = top-level
    agent_id      INTEGER NOT NULL REFERENCES agents(agent_id),
    content       TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 18. SHARED_TASKS — Todo partagé entre agents
-- ============================================================
CREATE TABLE IF NOT EXISTS shared_tasks (
    task_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL,
    description    TEXT,
    required_role  TEXT,                  -- rôle requis (NULL = tout le monde)
    context        TEXT DEFAULT 'general',-- contexte (ex: "project:modelweaver")
    status         TEXT DEFAULT 'TODO' CHECK(status IN ('TODO', 'IN_PROGRESS', 'DONE', 'FAILED', 'CANCELLED')),
    assigned_to    INTEGER REFERENCES agents(agent_id),
    parent_task_id INTEGER REFERENCES shared_tasks(task_id),
    priority       INTEGER DEFAULT 0,     -- plus haut = plus prioritaire
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 19. WATCHERS — Surveillants automatiques
-- ============================================================
CREATE TABLE IF NOT EXISTS watchers (
    watcher_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agents(agent_id),
    watch_type      TEXT NOT NULL CHECK(watch_type IN ('tasks', 'queue', 'chatroom', 'agents', 'successor_requests')),
    filter_criteria TEXT,
    interval_seconds INTEGER DEFAULT 60,
    last_checked_at TEXT,
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 20. AGENT_CONNECTIONS — Branchements persistants
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_connections (
    conn_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    channel     TEXT NOT NULL CHECK(channel IN ('chatroom', 'todo', 'queue', 'file', 'api', 'agent')),
    target_id   INTEGER,                 -- agent_id si channel='agent', NULL sinon
    config_json TEXT,                     -- {topic, path, url, ...}
    enabled     INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 21. SCHEDULED_JOBS — Tâches récurrentes / Planification
-- ============================================================
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER REFERENCES agents(agent_id) ON DELETE CASCADE,
    role_type       TEXT,
    skill           TEXT NOT NULL,
    request_payload TEXT,
    interval_seconds INTEGER,
    next_run_at     TEXT NOT NULL,
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 22. SYSTEM_STATE — Snapshot de l'OS au dernier check
-- ============================================================
CREATE TABLE IF NOT EXISTS system_state (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    os              TEXT,
    architecture    TEXT,
    os_version      TEXT,
    detected_managers TEXT,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================
-- 23. TOOL_USAGE — Télémétrie locale (opt-in phase 2/3)
-- ============================================================
CREATE TABLE IF NOT EXISTS tool_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    install_id  TEXT,
    outil_ref   TEXT,
    version_ref TEXT,
    recette_id  INTEGER,
    etat        TEXT CHECK(etat IN ('installed','uninstalled','upgraded')),
    ts          INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- USAGE & MESURE LOCAUX (modelweaver.db, privé, jamais poussé au distant)
-- ============================================================

-- Journal brut des appels LLM reels (FIFO). error_detail = JSON du corps
-- d'erreur (pour parser quel budget a sauté). Rotation sur created_at.
CREATE TABLE IF NOT EXISTS real_call_models (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_ref  TEXT,
    endpoint_id   INTEGER,
    key_ref       TEXT,
    model_ref     TEXT,
    agent_id      TEXT,
    sent_at       INTEGER NOT NULL,
    received_at   INTEGER,
    tokens_in     INTEGER DEFAULT 0,
    tokens_out    INTEGER DEFAULT 0,
    cost          REAL DEFAULT 0,
    status        TEXT CHECK(status IN ('ok','rate_limited','error','quota_exhausted')),
    error_code    TEXT,
    error_detail  TEXT,
    window_key    TEXT,
    created_at    INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_rcm_model ON real_call_models(model_ref);
CREATE INDEX IF NOT EXISTS idx_rcm_sent ON real_call_models(sent_at);
CREATE INDEX IF NOT EXISTS idx_rcm_status ON real_call_models(status);

-- Mesure réelle vs théorie (indicatif). method : explicit | correlated | pattern.
CREATE TABLE IF NOT EXISTS really_used_budget (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    budget_tag_code TEXT NOT NULL,
    target_type   TEXT NOT NULL,
    target_ref    TEXT NOT NULL,
    window        TEXT,
    measured_limit REAL,
    sample_count  INTEGER DEFAULT 0,
    first_exhausted_at INTEGER,
    confidence    REAL DEFAULT 0,
    method        TEXT,
    measured_at   INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(budget_tag_code, target_type, target_ref, window)
);

-- Usage live par endpoint x model (last_call, agent, rotation 10k).
CREATE TABLE IF NOT EXISTS endpoint_model_usage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id   INTEGER,
    model_ref     TEXT,
    agent_id      TEXT,
    requests      INTEGER DEFAULT 0,
    tokens_in     INTEGER DEFAULT 0,
    tokens_out    INTEGER DEFAULT 0,
    cost          REAL DEFAULT 0,
    last_call_at  INTEGER,
    last_call_working INTEGER DEFAULT 1,
    error_count   INTEGER DEFAULT 0,
    created_at    INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_emu_endpoint ON endpoint_model_usage(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_emu_model ON endpoint_model_usage(model_ref);

-- Consommation locale des budgets (used/window), pointe sur budgets.id (catalogue).
CREATE TABLE IF NOT EXISTS budget_consumption (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    budget_id     INTEGER NOT NULL,
    used          REAL DEFAULT 0,
    updated_at    INTEGER DEFAULT (strftime('%s','now'))
);

-- Efficacite observee PERSONNELLEMENT (critères propres de l'utilisateur).
CREATE TABLE IF NOT EXISTS local_model_efficacy (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    model_ref     TEXT NOT NULL,
    use_case      TEXT NOT NULL,
    score_quality   REAL DEFAULT 0,
    score_speed     REAL DEFAULT 0,
    score_cost      REAL DEFAULT 0,
    score_reliability REAL DEFAULT 0,
    samples       INTEGER DEFAULT 0,
    criteria_meta TEXT,
    last_evaluated_at INTEGER,
    UNIQUE(model_ref, use_case)
);

-- ============================================================
-- INDEXES
-- ============================================================
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_providers_ref ON providers(ref);
CREATE INDEX IF NOT EXISTS idx_models_ref ON models(ref);
CREATE INDEX IF NOT EXISTS idx_api_keys_provider ON api_keys(provider_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_identity ON api_keys(identity);
CREATE INDEX IF NOT EXISTS idx_provider_models_provider ON provider_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_provider_models_model ON provider_models(model_id);
CREATE INDEX IF NOT EXISTS idx_local_outils_ref ON local_outils(outil_ref);
CREATE INDEX IF NOT EXISTS idx_local_classes_ref ON classes_outils(ref);
CREATE INDEX IF NOT EXISTS idx_local_installs_outil ON local_installs(manager, status);
CREATE INDEX IF NOT EXISTS idx_jobs_next_run ON scheduled_jobs(next_run_at, enabled);
CREATE INDEX IF NOT EXISTS idx_wakeup_ticker ON wakeup_calls(status, execute_after);
CREATE INDEX IF NOT EXISTS idx_messages_session ON agent_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_agent ON sessions(agent_id, status);
CREATE INDEX IF NOT EXISTS idx_agents_provider ON agents(provider_id);
CREATE INDEX IF NOT EXISTS idx_queue_recipient ON agent_queue(to_agent_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_queue_broadcast ON agent_queue(topic, status, created_at);
CREATE INDEX IF NOT EXISTS idx_chatroom_thread ON chatroom_messages(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chatroom_agent ON chatroom_messages(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_role_context ON shared_tasks(required_role, context, status);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON shared_tasks(status, priority);
CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON shared_tasks(assigned_to, status);
CREATE INDEX IF NOT EXISTS idx_watchers_type ON watchers(watch_type, enabled);
CREATE INDEX IF NOT EXISTS idx_connections_agent ON agent_connections(agent_id, channel);
