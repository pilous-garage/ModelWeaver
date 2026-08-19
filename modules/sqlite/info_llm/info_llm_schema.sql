-- ============================================================
-- INFO_LLM.DB — données STABLES du bridge (V1).
-- Projection du local catalogue v4, régénérée par le COMPACTEUR.
-- Spec : docs/migration_sql.md §6.
--
-- PRINCIPE :
--   - Un SEUL writer : le compacteur (token write_info_llm, mini-batchs
--     upsert_many). Le bridge (threads agents) est en LECTURE SEULE.
--   - ids : INTEGER stables par réf (upsert par colonne UNIQUE, les ids
--     existants sont conservés entre régénérations).
--   - local_data_id : data_id v4 de provenance (traçabilité).
--   - Tables d'ANNOTATIONS (alias_model, budget_tags, budgets,
--     budget_generique_key_tag, budget_user_key_id) : saisies à la main,
--     JAMAIS écrasées par le compacteur.
--   - provider_endpoint_api_key_type / endpoint_apikeytype_model_adress :
--     schéma d'adresses (type-level, AUCUN secret — la clé vient du vault
--     keyring au runtime dans la table RAM `adress`).
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', 1);

-- ── 1. PROVIDERS (compacteur ← provider) ────────────────────
CREATE TABLE IF NOT EXISTS catalogue_providers (
    provider_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ref           TEXT NOT NULL UNIQUE,        -- ex. "openrouter"
    name          TEXT DEFAULT '',
    api_type      TEXT DEFAULT '',             -- openai | anthropic | ...
    doc           TEXT DEFAULT '',
    npm           TEXT DEFAULT '',
    env           TEXT DEFAULT '{}',           -- variables d'env JSON
    local_data_id INTEGER DEFAULT 0,           -- data_id v4 (provenance)
    created_at    TEXT DEFAULT (datetime('now'))
);

-- ── 2. ENDPOINTS (compacteur ← endpoint) ────────────────────
CREATE TABLE IF NOT EXISTS provider_endpoints (
    endpoint_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ref           TEXT NOT NULL UNIQUE,        -- ex. "openrouter/default"
    provider_id   INTEGER NOT NULL,
    endpoint_url  TEXT DEFAULT '',
    api_type      TEXT DEFAULT '',
    local_data_id INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- ── 3. MODÈLES OFFICIELS (compacteur ← model_official) ──────
CREATE TABLE IF NOT EXISTS catalogue_models (
    model_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ref           TEXT NOT NULL UNIQUE,        -- ex. "gpt-4o"
    description   TEXT DEFAULT '',
    family        TEXT DEFAULT '',             -- tag famille (gpt, claude…)
    local_data_id INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- ── 4. CAPACITÉS par modèle officiel (compacteur) ───────────
CREATE TABLE IF NOT EXISTS model_capability (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id        INTEGER NOT NULL REFERENCES catalogue_models(model_id),
    capability      TEXT NOT NULL,           -- vision, function_calling, embedding, streaming, chat, reasoning, json_mode, long_context
    value           TEXT NOT NULL DEFAULT 'unknown',  -- true|false|unknown
    confidence      REAL NOT NULL DEFAULT 0.5,       -- 0..1, source×age decay
    source_ref      TEXT NOT NULL DEFAULT '',      -- user|enterprise|official|models.dev|distant|friend|git_depot
    last_info       TEXT DEFAULT '',             -- version/date info
    exp_adjust      REAL NOT NULL DEFAULT 0,     -- Phase D: ajustage par expérience
    exp_n           INTEGER NOT NULL DEFAULT 0,  -- Phase D: nombre d'observations
    local_data_id   INTEGER DEFAULT 0,
    UNIQUE(model_id, capability)
);

CREATE INDEX IF NOT EXISTS idx_mc_model ON model_capability(model_id);
CREATE INDEX IF NOT EXISTS idx_mc_cap ON model_capability(capability);

-- ── 5. MODÈLES CHEZ UN PROVIDER (compacteur ← MPE) ──────────
CREATE TABLE IF NOT EXISTS provider_models (
    provider_model_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id         INTEGER NOT NULL,
    model_id            INTEGER NOT NULL,
    name                TEXT NOT NULL,          -- ref complet : "openrouter/openai/gpt-4o#free"
    provider_model_name TEXT DEFAULT '',        -- nom chez le provider (sans préfixe)
    key_tag             TEXT DEFAULT '',        -- "#free" → "free" | ""
    free_tier           INTEGER DEFAULT 0,
    context_window      INTEGER,
    status              TEXT DEFAULT 'active',
    local_data_id       INTEGER DEFAULT 0,
    created_at          TEXT DEFAULT (datetime('now')),
    UNIQUE(provider_id, name)
);

-- ── 6. MAPPING model_key → provider_models (compacteur) ─────
CREATE TABLE IF NOT EXISTS provider_models_mapping (
    mapping_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    model_key         TEXT NOT NULL,            -- "gpt-4o" (canonique)
    provider_model_id INTEGER NOT NULL REFERENCES provider_models(provider_model_id),
    UNIQUE(model_key, provider_model_id)
);

-- ── 7. MATRICE endpoint ↔ api_key_type (compacteur) ────────
-- Quels types de clé sont AUTORISÉS sur quel endpoint d'un provider.
-- Source : provider_typekey (local v4) ; 'unknown' si aucun déclaré.
-- C'est la matrice qui résout les {api_key} POSSIBLES pour une adresse
-- (la valeur réelle vient du vault keyring au runtime, jamais ici).
CREATE TABLE IF NOT EXISTS provider_endpoint_api_key_type (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id   INTEGER NOT NULL,
    provider_ref  TEXT DEFAULT '',
    endpoint_id   INTEGER NOT NULL,
    endpoint_ref  TEXT DEFAULT '',
    api_key_type  TEXT NOT NULL DEFAULT 'unknown',  -- free | paid | plus | unknown…
    local_data_id INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(provider_id, endpoint_id, api_key_type)
);

-- ── 8. SCHÉMA D'ADRESSES PRÉ-REMLI (compacteur, statique) ───
-- endpoint_apikeytype_model_adress = endpoint × api_key_type autorisé
-- × model_endpoint. Aucun secret : que des références de TYPE + dénorm
-- (URL, model_key, sdk). La clé EN CLAIR est résolue au runtime dans la
-- table RAM `adress` (vault keyring) — jamais sur HDD.
-- UNIQUE(endpoint_id, api_key_type, provider_id, model_endpoint_id)
CREATE TABLE IF NOT EXISTS endpoint_apikeytype_model_adress (
    adress_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id         INTEGER NOT NULL,
    endpoint_ref        TEXT DEFAULT '',
    endpoint_url        TEXT DEFAULT '',
    api_key_type        TEXT NOT NULL DEFAULT 'unknown',
    provider_id         INTEGER NOT NULL,
    provider_ref        TEXT DEFAULT '',
    model_endpoint_id   INTEGER NOT NULL,       -- → provider_models.provider_model_id
    model_id            INTEGER,
    model_key           TEXT DEFAULT '',
    provider_model_name TEXT DEFAULT '',
    sdk                 TEXT DEFAULT '',        -- api_type (openai_compatible, anthropic…)
    available           INTEGER DEFAULT 1,      -- annotation (état → runtime_llm)
    deprecated          INTEGER DEFAULT 0,      -- annotation
    local_data_id       INTEGER DEFAULT 0,
    created_at          TEXT DEFAULT (datetime('now')),
    UNIQUE(endpoint_id, api_key_type, provider_id, model_endpoint_id)
);

-- ── 8. ALIAS (ANNOTATION manuelle) ──────────────────────────
CREATE TABLE IF NOT EXISTS alias_model (
    alias_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id     INTEGER REFERENCES catalogue_models(model_id),
    source_name  TEXT NOT NULL,
    source       TEXT NOT NULL,
    source_type  TEXT NOT NULL CHECK(source_type IN ('provider','benchmark','manual')),
    confidence   TEXT DEFAULT 'auto',
    status       TEXT NOT NULL DEFAULT 'linked'
                 CHECK(status IN ('linked','unresolved','ambiguous')),
    updated_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(source, source_type, source_name)
);

-- ── 9. TARIFS par (modèle, key_tag) (compacteur ← typekey) ──
CREATE TABLE IF NOT EXISTS cost_final (
    cost_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id         INTEGER NOT NULL REFERENCES catalogue_models(model_id),
    provider_model_id INTEGER,                  -- NULL = tarif officiel
    key_tag          TEXT NOT NULL DEFAULT '',
    input_per_1m     REAL,                      -- USD / 1M tokens in
    output_per_1m    REAL,                      -- USD / 1M tokens out
    free_tier        INTEGER DEFAULT 0,
    local_data_id    INTEGER DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now')),
    UNIQUE(model_id, key_tag, provider_model_id)
);

-- ── 10. DÉFINITIONS DE BUDGETS (ANNOTATION manuelle) ────────
CREATE TABLE IF NOT EXISTS budget_tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    code  TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    unit  TEXT,
    scope TEXT  -- requests | tokens | cost | latency
);
INSERT OR IGNORE INTO budget_tags (code, label, unit, scope) VALUES
    ('req_per_min',   'Requetes / minute',         'requests', 'requests'),
    ('req_per_day',   'Requetes / jour',           'requests', 'requests'),
    ('tok_per_min',   'Tokens / minute',           'tokens',   'tokens'),
    ('tok_per_hour',  'Tokens / heure',            'tokens',   'tokens'),
    ('tok_per_day',   'Tokens / jour',             'tokens',   'tokens'),
    ('cost_per_day',  'Cout / jour (USD)',         'usd',      'cost'),
    ('cost_per_month','Cout / mois (USD)',         'usd',      'cost'),
    ('request',       'Requete (cout unitaire)',   'request',  'request'),
    ('money',         'Argent (USD)',              'usd',      'money'),
    ('time',          'Temps (secondes)',          'seconds',  'time'),
    ('thinking_power','Puissance de pensee (indice)', 'think', 'thinking_power');

-- Limites théoriques partageables (target_type : provider|model|endpoint|key)
CREATE TABLE IF NOT EXISTS budgets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL CHECK(target_type IN ('provider','model','endpoint','key')),
    target_ref  TEXT NOT NULL,
    tag_id      INTEGER NOT NULL REFERENCES budget_tags(id),
    limit_value REAL NOT NULL,
    window      TEXT NOT NULL DEFAULT 'day' CHECK(window IN ('minute','hour','day','month')),
    cost_per_unit REAL,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- Budgets TRACÉS par tag de clé (free/plus/premium) — définition sans état
CREATE TABLE IF NOT EXISTS budget_generique_key_tag (
    budget_generique_key_tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key_tag    TEXT NOT NULL DEFAULT '',     -- free | plus | premium | '' (générique)
    tag_id         INTEGER NOT NULL REFERENCES budget_tags(id),
    quota          REAL NOT NULL,
    souplesse      TEXT DEFAULT 'strict' CHECK(souplesse IN ('strict','souple','informatif')),
    souplesse_taux REAL DEFAULT 0,
    interval_reset TEXT NOT NULL DEFAULT 'day' CHECK(interval_reset IN ('minute','hour','day','month')),
    session_start  TEXT DEFAULT '',
    session_close  TEXT DEFAULT '',
    rolling_hours  INTEGER DEFAULT 0,
    created_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(api_key_tag, tag_id, interval_reset)
);

-- Budgets MANUELS (utilisateur/team) — définition sans état
CREATE TABLE IF NOT EXISTS budget_user_key_id (
    budget_user_key_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_ref       TEXT NOT NULL,
    tag_id         INTEGER NOT NULL REFERENCES budget_tags(id),
    quota          REAL NOT NULL,
    souplesse      TEXT DEFAULT 'strict' CHECK(souplesse IN ('strict','souple','informatif')),
    souplesse_taux REAL DEFAULT 0,
    interval_reset TEXT NOT NULL DEFAULT 'day' CHECK(interval_reset IN ('minute','hour','day','month')),
    session_start  TEXT DEFAULT '',
    session_close  TEXT DEFAULT '',
    rolling_hours  INTEGER DEFAULT 0,
    created_at     TEXT DEFAULT (datetime('now')),
    UNIQUE(user_ref, tag_id, interval_reset)
);

-- Index du bridge (sélection par ref)
CREATE INDEX IF NOT EXISTS idx_pm_provider ON provider_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_pm_model   ON provider_models(model_id);
CREATE INDEX IF NOT EXISTS idx_peakt_prov ON provider_endpoint_api_key_type(provider_id, api_key_type);
CREATE INDEX IF NOT EXISTS idx_eama_key   ON endpoint_apikeytype_model_adress(model_key);
CREATE INDEX IF NOT EXISTS idx_eama_prov  ON endpoint_apikeytype_model_adress(provider_id, api_key_type);
CREATE INDEX IF NOT EXISTS idx_eama_ep   ON endpoint_apikeytype_model_adress(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_cost_model ON cost_final(model_id);
CREATE INDEX IF NOT EXISTS idx_alias_name ON alias_model(source_name);
