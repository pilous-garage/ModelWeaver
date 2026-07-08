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
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_cat_providers_ref ON catalogue_providers(ref);
CREATE INDEX IF NOT EXISTS idx_cat_models_ref ON catalogue_models(ref);
CREATE INDEX IF NOT EXISTS idx_cat_models_developer ON catalogue_models(developer);
CREATE INDEX IF NOT EXISTS idx_cat_tools_ref ON catalogue_tools(ref);
CREATE INDEX IF NOT EXISTS idx_cat_tools_platform ON catalogue_tools(allowed_platforms);
CREATE INDEX IF NOT EXISTS idx_cat_commands_ref ON catalogue_commands(ref);
