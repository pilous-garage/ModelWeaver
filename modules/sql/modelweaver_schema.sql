-- ModelWeaver Local Database Schema
-- Fichier: .modelweaver/modelweaver.db
-- Contient l'état local : providers, clés, modèles, outils installés

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================
-- 1. PROVIDERS — Fournisseurs d'API
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
-- 5. TOOL_DEFINITIONS — Identité des outils (Réf unique)
-- ============================================================
CREATE TABLE IF NOT EXISTS tool_definitions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    tool_class      TEXT DEFAULT 'other',
    created_at      INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 5.1. TOOL_VARIANTS — Spécifications techniques par OS/Arch
-- ============================================================
CREATE TABLE IF NOT EXISTS tool_variants (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id         INTEGER NOT NULL REFERENCES tool_definitions(id) ON DELETE CASCADE,
    os              TEXT NOT NULL,
    architecture    TEXT NOT NULL,
    version         TEXT,
    manager         TEXT NOT NULL,
    size_download   INTEGER DEFAULT 0,
    size_disk       INTEGER DEFAULT 0,
    trust_score     REAL DEFAULT 1.0,
    is_official     INTEGER DEFAULT 0,
    install_count   INTEGER DEFAULT 0,
    uninstall_count INTEGER DEFAULT 0,
    updated_at      INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(tool_id, os, architecture, manager)
);

-- ============================================================
-- 5.5. PACKAGE_MANAGERS — Gestionnaires de paquets OS détectés
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

-- Seed des gestionnaires connus
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
-- 5.6. TOOL_CLASSES — Catégories d'outils
-- ============================================================
CREATE TABLE IF NOT EXISTS tool_classes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT UNIQUE NOT NULL,
    label       TEXT NOT NULL,
    sort_order  INTEGER DEFAULT 0,
    created_at  INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at  INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 6. TOOL_PACKAGES — Versions/packages des outils
-- ============================================================
CREATE TABLE IF NOT EXISTS tool_packages (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id             INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    version             TEXT NOT NULL,
    platform            TEXT,
    arch                TEXT,
    download_path       TEXT,
    download_url        TEXT,
    size_bytes          INTEGER,
    checksum            TEXT,
    integrity_status    TEXT DEFAULT 'pending',
    download_timestamp  INTEGER,
    installed_at        INTEGER,
    install_strategy    TEXT,
    install_cmd_template TEXT,
    install_result_log  TEXT,
    UNIQUE(tool_id, version, platform, arch)
);

-- ============================================================
-- 7. PACKAGE_DEPENDENCIES — Dépendances entre packages
-- ============================================================
CREATE TABLE IF NOT EXISTS package_dependencies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id          INTEGER NOT NULL REFERENCES tool_packages(id) ON DELETE CASCADE,
    dep_type            TEXT NOT NULL CHECK(dep_type IN ('system_pkg', 'python_pkg', 'binary_tool')),
    identifier          TEXT NOT NULL,
    install_cmd         TEXT NOT NULL,
    version_constraint  TEXT,
    required_by_default INTEGER DEFAULT 1
);

-- ============================================================
-- 8. LOCAL_TOOLS — Outils installés localement
-- ============================================================
CREATE TABLE IF NOT EXISTS local_tools (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id     INTEGER NOT NULL REFERENCES tool_definitions(id) ON DELETE CASCADE,
    version     TEXT NOT NULL,
    install_path TEXT,
    status      TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'installed', 'failed', 'outdated', 'uninstalled')),
    installed_at INTEGER,
    updated_at  INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(tool_id)
);

-- ============================================================
-- 9. LOCAL_LLMS — LLM téléchargés localement (Ollama, etc.)
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
-- 10. COMMANDS — Utilitaires non triviaux pour les IA
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
-- 11. INSTALL_JOB_LOG — Historique d'installation
-- ============================================================
CREATE TABLE IF NOT EXISTS install_job_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id         INTEGER REFERENCES tool_definitions(id),
    package_id      INTEGER REFERENCES tool_packages(id),
    job_timestamp   INTEGER DEFAULT (strftime('%s', 'now')),
    job_type        TEXT NOT NULL CHECK(job_type IN ('download', 'install', 'uninstall', 'verify')),
    status          TEXT NOT NULL CHECK(status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    stdout_capture  TEXT,
    stderr_capture  TEXT,
    duration_ms     INTEGER,
    agent_details   TEXT
);

-- ============================================================
-- 12. TOOL_CONFIG — Configuration des outils (scopes)
-- ============================================================
CREATE TABLE IF NOT EXISTS tool_config (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id     INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
    config_key  TEXT NOT NULL,
    config_value TEXT,
    scope       TEXT DEFAULT 'user',
    created_at  INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at  INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(tool_id, config_key, scope)
);

-- ============================================================
-- 13. MODEL_PROVIDERS — Ressources hardware/cloud pour les agents
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
-- 14. AGENTS — Identité et couplage modèle/hardware
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
-- 15. SESSIONS — Fils de discussion persistants
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
-- 16. AGENT_MESSAGES — Mémoire brute au format OpenAI
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
-- 17. WAKEUP_CALLS — Système nerveux (tâches et réveils)
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
-- 18. AGENT_QUEUE — Messagerie inter-agents (direct + broadcast)
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
-- 19. CHATROOM_MESSAGES — Board public avec threads
-- ============================================================
CREATE TABLE IF NOT EXISTS chatroom_messages (
    message_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id     INTEGER REFERENCES chatroom_messages(message_id),  -- NULL = top-level
    agent_id      INTEGER NOT NULL REFERENCES agents(agent_id),
    content       TEXT NOT NULL,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 20. SHARED_TASKS — Todo partagé entre agents
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
-- 21. WATCHERS — Surveillants automatiques
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
-- 22. AGENT_CONNECTIONS — Branchements persistants
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
-- 23. SCHEDULED_JOBS — Tâches récurrentes / Planification
-- ============================================================
CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER REFERENCES agents(agent_id) ON DELETE CASCADE,
    role_type       TEXT,                    -- Si NULL, lié à l'agent_id. Sinon, n'importe quel agent du rôle.
    skill           TEXT NOT NULL,           -- Skill à déclencher
    request_payload TEXT,                    -- Payload de la wakeup_call
    interval_seconds INTEGER,                -- Intervalle de répétition (0 = one-shot)
    next_run_at     TEXT NOT NULL,           -- Date/Heure du prochain déclenchement
    enabled         INTEGER DEFAULT 1,       -- 1 = actif, 0 = pause
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 24. TOOL_METRICS — Poids mesurés des variantes d'outils
-- ============================================================
CREATE TABLE IF NOT EXISTS tool_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_id         TEXT NOT NULL,
    version         TEXT NOT NULL,
    os              TEXT NOT NULL,
    arch            TEXT NOT NULL,
    manager         TEXT NOT NULL,
    size_download   INTEGER DEFAULT 0,
    size_disk       INTEGER DEFAULT 0,
    last_measured   DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tool_id, version, os, arch, manager)
);

CREATE INDEX IF NOT EXISTS idx_jobs_next_run ON scheduled_jobs(next_run_at, enabled);
CREATE INDEX IF NOT EXISTS idx_providers_ref ON providers(ref);
CREATE INDEX IF NOT EXISTS idx_models_ref ON models(ref);
CREATE INDEX IF NOT EXISTS idx_api_keys_provider ON api_keys(provider_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_identity ON api_keys(identity);
CREATE INDEX IF NOT EXISTS idx_provider_models_provider ON provider_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_provider_models_model ON provider_models(model_id);
CREATE INDEX IF NOT EXISTS idx_tools_ref ON tool_definitions(ref);
CREATE INDEX IF NOT EXISTS idx_tools_priority ON tool_variants(is_official);
CREATE INDEX IF NOT EXISTS idx_local_tools_tool ON local_tools(tool_id);
CREATE INDEX IF NOT EXISTS idx_jobs_timestamp ON install_job_log(job_timestamp);
CREATE INDEX IF NOT EXISTS idx_packages_tool ON tool_packages(tool_id);
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
