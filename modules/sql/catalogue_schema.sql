-- ModelWeaver Catalogue Database Schema
-- Fichier: .modelweaver/catalogue.db
-- Référence publique, peut être synchronisée depuis une BDD distante (Turso).

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================
-- 1. CATALOGUE_PROVIDERS — Fournisseurs LLM (référence)
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
-- 2. CATALOGUE_MODELS — Modèles LLM (référence)
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
-- 3. PROVIDER_MODELS — Jointure provider ↔ modèle (prix, tokens)
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
    status              TEXT DEFAULT 'active' CHECK(status IN ('active','deprecated','experimental')),
    created_at          INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at          INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(provider_id, model_id)
);

-- ============================================================
-- 4. CATALOGUE_COMMANDS — Commandes utilitaires
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
-- 5. CLASSES_OUTILS — Taxonomie métier des outils
--    (language, dev-tool, ide, chat-llm, agent, engine, router,
--     context, system, other). Différent du tool_type technique
--    (binary/python-module/...) qui reste dans catalogue_outils.
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
-- 6. CATALOGUE_OUTILS — Entrée catalogue par outil
--    default_download_url / allowed_platforms / allowed_arches
--    sont désormais INFÉRÉS des recettes (catalogue_recettes).
--    install_method est dans catalogue_recettes.manager.
--    tool_type (binary/python-module/...) reste pour le routing installer.
--    classe_outil_id pointe vers la classe métier (classes_outils).
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_outils (
    outil_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT UNIQUE NOT NULL,
    nom             TEXT NOT NULL,
    fabricant       TEXT,
    description     TEXT,
    tool_type       TEXT CHECK(tool_type IN ('binary','python-module','archive','source','container')),
    classe_outil_id INTEGER REFERENCES classes_outils(classe_id) ON DELETE SET NULL,
    created_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 6. CATALOGUE_VERSIONS — Une version par outil
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
-- 7. CATALOGUE_RECETTES — Recette par (version, os, arch, manager)
--    content = corps de la recette (.mw.yaml du manager block)
--    Chaque colonne manager est l'install_method (pip, apt, binary…).
--    L'existence d'une recette pour un (os, arch) donné indique
--    la compatibilité (plus besoin de allowed_platforms/arches).
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_recettes (
    recette_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id      INTEGER NOT NULL REFERENCES catalogue_versions(version_id) ON DELETE CASCADE,
    os              TEXT NOT NULL DEFAULT 'all',
    arch            TEXT NOT NULL DEFAULT 'all',
    manager         TEXT,          -- install_method : pip, apt, binary, github-release…
    package         TEXT,          -- nom du paquet chez le manager (ex: litellm)
    confidence      REAL DEFAULT 1.0,
    createur_id     TEXT DEFAULT 'system',
    install_count   INTEGER DEFAULT 0,
    uninstall_count INTEGER DEFAULT 0,
    content         TEXT,
    enabled         INTEGER DEFAULT 1,
    created_at      INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at      INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(version_id, os, arch, manager)
);

-- ============================================================
-- 8. OUTILS_POPULARITE — Table distante (agrégée par outil)
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
CREATE INDEX IF NOT EXISTS idx_cat_commands_ref ON catalogue_commands(ref);
CREATE INDEX IF NOT EXISTS idx_cat_provider_models_provider ON provider_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_cat_provider_models_model ON provider_models(model_id);
CREATE INDEX IF NOT EXISTS idx_outils_ref ON catalogue_outils(ref);
CREATE INDEX IF NOT EXISTS idx_classes_ref ON classes_outils(ref);
CREATE INDEX IF NOT EXISTS idx_recettes_version ON catalogue_recettes(version_id);
