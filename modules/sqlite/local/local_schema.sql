-- ============================================================
-- LOCAL_CATALOGUE.DB — Référentiel méta des données (V4).
-- Spec : docs/local_catalogue_spec.md (2026-08-17).
--
-- SCHÉMA D'INFRA — tables fixes du domaine `local`.
-- Les tables PAR TYPE ({type}_data/_tag/_tag_type/_source_and_sharing)
-- sont créées dynamiquement par create_new_data_type() (local.py) après
-- validation du data_value_type — voir BASE_COLUMNS / create_type.
--
-- PRINCIPE (V4 — modèle versionné multi-sources) :
--   - Une data = un quadruplé (namespace, name, source_id, version) UNIQUE.
--     Chaque entrée a son propre data_id = hash stable du quadruplé.
--   - Les sources sont déclarées (global_local_source + mirroir_sources) :
--     user, official, enterprise, distant, friend, git_depot…
--   - Accès par sélection : tag d'abord, puis préférence de source
--     (ex. user>enterprise>official), puis tri de version (newest/oldest/
--     version littérale) — syntaxe catalogue.<type>.<ns>...<name>
--     [:source@version][:tag(nom)].
--   - Un writer ne modifie que les lignes de SA source : modifier une data
--     d'une source étrangère crée une NOUVELLE entrée (sa source, sa
--     version), jamais d'écrasement. Nettoyage : prune par source (keep=N).
--   - NON-bloquant : lectures mode=ro ; UN SEUL writer (token
--     write_catalogue) en mini-batchs ; une donnée un peu périmée est
--     acceptable.
--   - Écritures externes via le domaine `buffer` (buffer.db) : l'importeur
--     dépose des ops pending (token write_buffer) ; le consumer du domaine
--     local (local/write.consume_buffer) les applique ici en mini-batchs
--     (jamais bloquant).
--   - v3 : buffer déplacé dans son domaine séparé (buffer.db). v4 : modèle
--     versionné multi-sources (reset).
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- Tables v2/v3 déplacées ou remplacées (reset, pas de migration).
DROP TABLE IF EXISTS global_local_buffer_op;

-- 0. META
CREATE TABLE IF NOT EXISTS global_local_meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO global_local_meta(key, value) VALUES ('schema_version', 4);

-- 0b. SOURCES (d'où vient une data : user, official, distant…)
CREATE TABLE IF NOT EXISTS global_local_source (
    sources_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    type_source TEXT NOT NULL
                CHECK(type_source IN ('user','official','enterprise',
                                      'distant','friend','git_depot')),
    ref         TEXT NOT NULL UNIQUE,
    label       TEXT DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO global_local_source(type_source, ref, label) VALUES
    ('user',       'user',        'données locales (écritures du writer local)'),
    ('official',   'official',    'source officielle (éditeur/projet)'),
    ('enterprise', 'enterprise',  'catalogue de l''entreprise'),
    ('distant',    'distant',     'catalogue distant non officiel'),
    ('friend',     'friend',      'catalogue d''un ami'),
    ('git_depot',  'git_depot',   'dépôt git importé');

CREATE TABLE IF NOT EXISTS global_local_mirror_sources (
    mirror_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    sources_id  INTEGER NOT NULL REFERENCES global_local_source(sources_id)
                        ON DELETE CASCADE,
    address     TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(sources_id, address)
);
CREATE INDEX IF NOT EXISTS idx_gl_mirror_src ON global_local_mirror_sources(sources_id);

-- 1. DATA_TYPE (registre des types connus)
CREATE TABLE IF NOT EXISTS global_local_data_type (
    data_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL UNIQUE,
    description  TEXT DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO global_local_data_type(code, description) VALUES
    ('fichier', 'fichiers sources suivis (mtime+hash)'),
    ('skill',   'skills du catalogue (AgentsCatalogue/skills)'),
    ('agent',   'agents référencés par les teams (yaml)'),
    ('team',    'teams (manifests services/manifests/teams)');

-- 2. NAMESPACE (arbre imbriqué ; racine = parent NULL)
CREATE TABLE IF NOT EXISTS global_local_namespace (
    ns          TEXT PRIMARY KEY,
    parent      TEXT REFERENCES global_local_namespace(ns) ON DELETE SET NULL,
    description TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gl_ns_parent ON global_local_namespace(parent);

-- 3. SHARED_DEFAULT (partage par défaut PAR DATA_TYPE)
CREATE TABLE IF NOT EXISTS global_local_shared_default (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    data_type_id  INTEGER NOT NULL REFERENCES global_local_data_type(data_type_id)
                       ON DELETE CASCADE,
    tag_type      TEXT NOT NULL DEFAULT '*',
    tag_value     TEXT NOT NULL DEFAULT '*',
    can_be_shared TEXT NOT NULL DEFAULT 'non'
              CHECK(can_be_shared IN ('non','everyone','enterprise','friends','official')),
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(data_type_id, tag_type, tag_value)
);

-- 4. PATH (chemins symboliques ; résolution par précision gauche→droite)
CREATE TABLE IF NOT EXISTS global_local_path (
    path_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    path_name  TEXT NOT NULL UNIQUE,
    address    TEXT NOT NULL,
    scheme     TEXT NOT NULL DEFAULT 'file',
    description TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gl_path_scheme ON global_local_path(scheme);

-- 5. PRIVILEGES (famille SECURITY — refus par chemin/kind/agent/team/niveau)
CREATE TABLE IF NOT EXISTS global_local_privilege (
    id_auth      INTEGER PRIMARY KEY AUTOINCREMENT,
    chemin_ref   TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'path'
                 CHECK(kind IN ('ref','path','cmd')),
    agent_id     INTEGER NOT NULL DEFAULT -1,
    team         INTEGER NOT NULL DEFAULT -1,
    level        INTEGER NOT NULL DEFAULT 1,
    read         TEXT NOT NULL DEFAULT '----',
    write        TEXT NOT NULL DEFAULT '----',
    exec         TEXT NOT NULL DEFAULT '----',
    privileged   TEXT NOT NULL DEFAULT '----',
    ask          TEXT NOT NULL DEFAULT 'none'
                 CHECK(ask IN ('none','security_supervisor','human','human_root')),
    deadline     TEXT DEFAULT NULL,
    description  TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gl_priv_kind ON global_local_privilege(kind, chemin_ref);
CREATE INDEX IF NOT EXISTS idx_gl_priv_agent ON global_local_privilege(agent_id, team);
CREATE INDEX IF NOT EXISTS idx_gl_priv_deadline ON global_local_privilege(deadline);

CREATE TABLE IF NOT EXISTS global_local_privilege_condition (
    condition_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    id_auth        INTEGER NOT NULL REFERENCES global_local_privilege(id_auth)
                       ON DELETE CASCADE,
    condition_type TEXT NOT NULL,
    valeur         TEXT,
    compteur       INTEGER DEFAULT NULL,
    last_use_at    TEXT DEFAULT NULL,
    created_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gl_pcond_auth
    ON global_local_privilege_condition(id_auth);

-- 6. SECURITY_SUPERVISOR (agents superviseurs pour ask=security_supervisor)
CREATE TABLE IF NOT EXISTS global_local_security_supervisor (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    supervisor_agent_id INTEGER NOT NULL,
    scope              TEXT NOT NULL DEFAULT 'global'
                       CHECK(scope IN ('team','global')),
    team_id            INTEGER DEFAULT NULL,
    active             INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gl_sup_scope
    ON global_local_security_supervisor(scope, team_id);