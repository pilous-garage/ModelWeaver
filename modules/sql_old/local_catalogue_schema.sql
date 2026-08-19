-- ============================================================
-- LOCAL_CATALOGUE.DB — Référentiel méta des données (V2, reset).
-- Spec : docs/local_catalogue_spec.md (2026-08-17).
--
-- PRINCIPE :
--   - Données LÉGÈRES et typées (data_value_type). Pas de contenu lourd :
--     `file` = référence seule (ref_file), le disque est la seule vérité.
--   - Non-bloquant : lectures mode=ro (ou SQLite direct), UN SEUL writer
--     (token write_catalogue) en mini-batchs. Une donnée un peu périmée
--     est acceptable.
--   - data_id = hash stable(ref) par type (pas d'AUTOINCREMENT) : identique
--     entre load/reload ; couple (data_type_id, data_id) unique.
--   - Tables PAR TYPE créées à chaud par le writer (create_type), schéma
--     socle identique (voir BASE_COLUMNS dans catalogue_local.py).
--   - Écritures EXTERNES via global_local_buffer_op (payload pending) puis
--     appliquées par le consumer du writer (mini-batch, jamais bloquant).
--
-- MIGRATION : RESET COMPLET volontaire (pas de renommage) — voir bloc ci-dessous.
-- ============================================================

-- ============================================================
-- 0. RESET — ancien schéma V1 (tables recréées au nouveau modèle,
--    données ré-importées par les scripts de remplissage).
-- ============================================================
DROP TABLE IF EXISTS catalogues;
DROP TABLE IF EXISTS namespaces;
DROP TABLE IF EXISTS shared_default;
DROP TABLE IF EXISTS last_access_data;
DROP TABLE IF EXISTS last_modify_data;
DROP TABLE IF EXISTS catalogue_path;
DROP TABLE IF EXISTS bench_scores;
DROP TABLE IF EXISTS privileges;
DROP TABLE IF EXISTS privilege_conditions;
DROP TABLE IF EXISTS security_supervisor;
DROP TABLE IF EXISTS fichier_catalogue;
DROP TABLE IF EXISTS fichier_tags;
DROP TABLE IF EXISTS fichier_tag_types;
DROP TABLE IF EXISTS fichier_limites;
DROP TABLE IF EXISTS fichier_source_and_sharing;
DROP TABLE IF EXISTS skill_catalogue;
DROP TABLE IF EXISTS skill_tags;
DROP TABLE IF EXISTS skill_tag_types;
DROP TABLE IF EXISTS skill_limites;
DROP TABLE IF EXISTS skill_source_and_sharing;
DROP TABLE IF EXISTS meta;

-- ============================================================
-- 1. META — Versioning
-- ============================================================
CREATE TABLE IF NOT EXISTS global_local_meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO global_local_meta(key, value) VALUES ('schema_version', 2);

-- ============================================================
-- 2. DATA_TYPE — Registre des types de données du catalogue.
--    Chaque ligne = un type (fichier, skill, agent, team, provider…) dont
--    les tables {type}_data/_tag/_tag_type/_source_and_sharing existent
--    (créées à chaud par le writer via create_type, ou en seed).
-- ============================================================
CREATE TABLE IF NOT EXISTS global_local_data_type (
    data_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
    code         TEXT NOT NULL UNIQUE,
    description  TEXT DEFAULT '',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);

-- Types connus V1 (seed) : fichier, skill, agent, team.
-- provider / model / endpoint : créés par le script models.dev (buffer).
INSERT OR IGNORE INTO global_local_data_type(code, description) VALUES
    ('fichier', 'fichiers sources suivis (mtime+hash)'),
    ('skill',   'skills du catalogue (AgentsCatalogue/skills)'),
    ('agent',   'agents référencés par les teams (yaml)'),
    ('team',    'teams (manifests services/manifests/teams)');

-- ============================================================
-- 3. NAMESPACE — Arborescence des namespaces imbriqués.
--    parent REFERENCES global_local_namespace(ns). Racine = parent NULL.
--    Tri + hiérarchie des data (résolution catalogue.a.b.fn).
-- ============================================================
CREATE TABLE IF NOT EXISTS global_local_namespace (
    ns          TEXT PRIMARY KEY,
    parent      TEXT REFERENCES global_local_namespace(ns) ON DELETE SET NULL,
    description TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gl_ns_parent ON global_local_namespace(parent);

-- ============================================================
-- 4. SHARED_DEFAULT — Partage par défaut PAR DATA_TYPE.
--    Résolution "can_be_shared" en cascade :
--      (data_type_id, tag_type, tag_value) → (data_type_id, '*', '*')
--    Tag '*' = appliqué à toutes les entrées du type.
-- ============================================================
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

-- ============================================================
-- 5. PATH — Environnement de chemins symboliques.
--    path (dans une data) = SYMBOLIQUE ; résolu via path_name → address.
-- ============================================================
CREATE TABLE IF NOT EXISTS global_local_path (
    path_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    path_name  TEXT NOT NULL UNIQUE,
    address    TEXT NOT NULL,
    scheme     TEXT NOT NULL DEFAULT 'file',
    description TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gl_path_scheme ON global_local_path(scheme);

-- ============================================================
-- 6. BUFFER — Tampon des opérations externes (grosses modifs, modifs
--    externes). Un importeur dépose ses ops 'pending' en UNE écriture
--    batch ; le consumer du writer les applique en mini-batchs (jamais
--    bloquant). Les ops 'applied' sont conservées (audit).
-- ============================================================
CREATE TABLE IF NOT EXISTS global_local_buffer_op (
    op_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    direction     TEXT NOT NULL DEFAULT 'in'
                  CHECK(direction IN ('in','out')),
    domain        TEXT NOT NULL,             -- data_type code cible
    op            TEXT NOT NULL
                  CHECK(op IN ('add','modify','delete','refresh')),
    payload_json  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending','applied','error','cancelled')),
    error         TEXT DEFAULT '',
    external_tag  TEXT DEFAULT '',           -- 'models.dev', 'manual', 'community'…
    ref_external  TEXT DEFAULT '',           -- ref côté source
    created_at    TEXT DEFAULT (datetime('now')),
    applied_at    TEXT DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_gl_buf_status ON global_local_buffer_op(status, domain);
CREATE INDEX IF NOT EXISTS idx_gl_buf_ext ON global_local_buffer_op(external_tag, status);

-- ============================================================
-- 7. PRIVILEGES (famille SECURITY) — Autorisations par chemin.
--    Schéma identique à la V1 (référentiel des autorisations) :
--    chemin_ref : ref canonique (kind='ref'), path (kind='path') ou
--    COMMANDE (kind='cmd'). agent_id/team : -1 = tous.
--    read/write/exec/privileged = 4 GROUPES de niveaux ('r-r--').
--    ask : 'none' | 'security_supervisor' | 'human' | 'human_root'.
-- ============================================================
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

-- ============================================================
-- 7b. PRIVILEGE_CONDITIONS (famille SECURITY).
--    condition_type : nb_times | lastcall | until_restart | until_date | ref_id
-- ============================================================
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

-- ============================================================
-- 7c. SECURITY_SUPERVISOR (famille SECURITY) — agents superviseurs
--    désignés pour les demandes ask=security_supervisor.
-- ============================================================
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

-- ============================================================
-- 8. TABLES PAR TYPE — créées à chaud par le writer (create_type).
--    Schéma socle BASE (défini dans catalogue_local.py) :
--
-- {type}_data :
--   data_id        INTEGER PRIMARY KEY      -- hash stable(ref), PAS d'auto
--   ref            TEXT UNIQUE NOT NULL     -- utils/bubble_sort@v1
--   name / namespace / version('latest')
--   data_value_type TEXT NOT NULL           -- text[n]|string|int|uint|float|bool|
--                                           -- date|timestamp|json|file|row(...)
--   value          TEXT DEFAULT '{}'        -- scalaire/json (row=plat); PAS lourd
--   ref_file / path / description / status('active')
--   last_modify    TEXT                     -- seule trace (writer seul)
--   created_at / updated_at
--   + colonnes dynamiques data_value_* (uniquement si row)
--
-- {type}_tag : (data_id FK CASCADE, tag_type, tag_value) UNIQUE
-- {type}_tag_type : tag_type PK, tag_value_type
--                   (bool|text|number|date|list|range) DEFAULT 'text'
-- {type}_source_and_sharing : data_id PK FK CASCADE, source, source_url,
--                   is_from_share, is_it_shared, can_be_shared, shared_at
-- ============================================================