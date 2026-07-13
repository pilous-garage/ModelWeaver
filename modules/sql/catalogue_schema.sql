-- ModelWeaver Catalogue Database Schema
-- Fichier: .modelweaver/catalogue.db
-- Référence publique, peut être synchronisée depuis une BDD distante
-- Uniquement les infos nécessaires pour confirmer l'existence et ajouter

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================
-- 1. CATALOGUE_PROVIDERS
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_providers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ref                TEXT UNIQUE NOT NULL,
    name               TEXT NOT NULL,
    provider_type      TEXT NOT NULL CHECK(provider_type IN ('cloud', 'local', 'ollama', 'builtin')),
    api_type           TEXT,
    website            TEXT,
    is_free_tier_provider INTEGER DEFAULT 0,
    created_at         INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 2. CATALOGUE_MODELS
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_models (
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
    parent_model_ref TEXT,
    created_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 3. CATALOGUE_TOOLS — Avec restriction système
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_tools (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ref                 TEXT UNIQUE NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT,
    tool_type           TEXT NOT NULL CHECK(tool_type IN ('binary', 'python-module', 'archive', 'source', 'container')),
    install_method      TEXT NOT NULL,
    current_version     TEXT,
    default_download_url TEXT,
    class               TEXT DEFAULT 'other',
    allowed_platforms   TEXT,
    allowed_arches      TEXT,
    created_at          INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 4. CATALOGUE_COMMANDS
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    command_type TEXT NOT NULL CHECK(command_type IN ('system', 'binary', 'project', 'utility')),
    created_at  INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 5. PROVIDER_MODELS — Jointure entre providers et modèles
-- ============================================================
CREATE TABLE IF NOT EXISTS provider_models (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id         INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
    model_id            INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
    provider_model_name TEXT NOT NULL,
    context_window_tokens INTEGER,
    max_output_tokens   INTEGER,
    cost_per_input_token  TEXT,
    cost_per_output_token TEXT,
    status              TEXT DEFAULT 'active' CHECK(status IN ('active', 'deprecated', 'experimental')),
    created_at          INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at          INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(provider_id, model_id)
);

-- ============================================================
-- 6. CATALOGUE_OUTILS — entrée catalogue par outil
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_outils (
    outil_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT UNIQUE NOT NULL,
    nom         TEXT NOT NULL,
    fabricant   TEXT,
    description TEXT,
    created_at  INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 7. CATALOGUE_VERSIONS — une version par outil
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_versions (
    version_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    outil_id    INTEGER NOT NULL REFERENCES catalogue_outils(outil_id) ON DELETE CASCADE,
    nom_version TEXT NOT NULL,
    description TEXT,
    created_at  INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(outil_id, nom_version)
);

-- ============================================================
-- 8. CATALOGUE_RECETTES — une recette par (version, os, arch, manager)
--    content = corps de la recette (.mw.yaml du manager block)
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_recettes (
    recette_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id      INTEGER NOT NULL REFERENCES catalogue_versions(version_id) ON DELETE CASCADE,
    os              TEXT NOT NULL DEFAULT 'all',
    arch            TEXT NOT NULL DEFAULT 'all',
    manager         TEXT,
    package         TEXT,
    confidence      REAL DEFAULT 1.0,
    createur_id     TEXT DEFAULT 'system',
    install_count   INTEGER DEFAULT 0,
    uninstall_count INTEGER DEFAULT 0,
    content         TEXT,
    enabled         INTEGER DEFAULT 1,
    created_at      INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 9. OUTILS_POPULARITE — table "distante" (synchro catalogue)
--    compte d'install/désinstall agrégés par outil
-- ============================================================
CREATE TABLE IF NOT EXISTS outils_popularite (
    outil_id      INTEGER PRIMARY KEY REFERENCES catalogue_outils(outil_id) ON DELETE CASCADE,
    nb_install    INTEGER DEFAULT 0,
    nb_desinstall INTEGER DEFAULT 0,
    updated_at    INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_cat_providers_ref ON catalogue_providers(ref);
CREATE INDEX IF NOT EXISTS idx_cat_models_ref ON catalogue_models(ref);
CREATE INDEX IF NOT EXISTS idx_cat_models_developer ON catalogue_models(developer);
CREATE INDEX IF NOT EXISTS idx_cat_tools_ref ON catalogue_tools(ref);
CREATE INDEX IF NOT EXISTS idx_cat_tools_platform ON catalogue_tools(allowed_platforms);
CREATE INDEX IF NOT EXISTS idx_cat_commands_ref ON catalogue_commands(ref);
CREATE INDEX IF NOT EXISTS idx_cat_provider_models_provider ON provider_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_cat_provider_models_model ON provider_models(model_id);
CREATE INDEX IF NOT EXISTS idx_recettes_version ON catalogue_recettes(version_id);
CREATE INDEX IF NOT EXISTS idx_outils_ref ON catalogue_outils(ref);
