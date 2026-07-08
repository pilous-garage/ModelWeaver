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
-- 5. TOOLS — Outils du catalogue (référence)
-- ============================================================
CREATE TABLE IF NOT EXISTS tools (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ref                TEXT UNIQUE NOT NULL,
    name               TEXT NOT NULL,
    description        TEXT,
    tool_type          TEXT NOT NULL CHECK(tool_type IN ('binary', 'python-module', 'archive', 'source', 'container')),
    install_method     TEXT NOT NULL CHECK(install_method IN ('direct-url', 'installer-script', 'package-manager', 'github-release', 'pip', 'apt', 'brew', 'winget')),
    current_version    TEXT,
    default_download_url TEXT,
    checksum_algorithm TEXT DEFAULT 'sha256',
    is_core            INTEGER DEFAULT 0,
    allowed_platforms  TEXT,
    allowed_arches     TEXT,
    installer_params   TEXT,
    fallback_chain     TEXT,
    catalogue_ref      TEXT,
    created_at         INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at         INTEGER DEFAULT (strftime('%s', 'now'))
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
    tool_id     INTEGER NOT NULL REFERENCES tools(id) ON DELETE CASCADE,
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
    tool_id         INTEGER REFERENCES tools(id),
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
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_providers_ref ON providers(ref);
CREATE INDEX IF NOT EXISTS idx_models_ref ON models(ref);
CREATE INDEX IF NOT EXISTS idx_api_keys_provider ON api_keys(provider_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_identity ON api_keys(identity);
CREATE INDEX IF NOT EXISTS idx_provider_models_provider ON provider_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_provider_models_model ON provider_models(model_id);
CREATE INDEX IF NOT EXISTS idx_tools_ref ON tools(ref);
CREATE INDEX IF NOT EXISTS idx_tools_priority ON tools(is_core);
CREATE INDEX IF NOT EXISTS idx_local_tools_tool ON local_tools(tool_id);
CREATE INDEX IF NOT EXISTS idx_jobs_timestamp ON install_job_log(job_timestamp);
CREATE INDEX IF NOT EXISTS idx_packages_tool ON tool_packages(tool_id);
