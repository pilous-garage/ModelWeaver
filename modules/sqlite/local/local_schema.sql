-- ============================================================
-- LOCAL_CATALOGUE.DB — Référentiel méta des données (V2).
-- Spec : docs/local_catalogue_spec.md (2026-08-17).
--
-- SCHÉMA D'INFRA — tables fixes du domaine `local`.
-- Les tables PAR TYPE ({type}_data/_tag/_tag_type/_source_and_sharing)
-- sont créées dynamiquement par create_new_data_type() (local.py) après
-- validation du data_value_type — voir BASE_COLUMNS / create_type.
--
-- PRINCIPE :
--   - Données LÉGÈRES et typées (data_value_type). Le disque est la seule
--     vérité pour `file` ; value = référence seulement.
--   - Non-bloquant : lectures mode=ro ; UN SEUL writer (token write_catalogue)
--     en mini-batchs. Une donnée un peu périmée est acceptable.
--   - data_id = hash stable(ref) par type (int64) — identique load/reload.
--   - Écritures externes via global_local_buffer_op (payload pending) puis
--     appliquées par process_buffer (mini-batch, jamais bloquant).
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- 0. META
CREATE TABLE IF NOT EXISTS global_local_meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO global_local_meta(key, value) VALUES ('schema_version', 2);

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

-- 5. BUFFER (tampon des opérations externes, jamais bloquant)
CREATE TABLE IF NOT EXISTS global_local_buffer_op (
    op_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    direction     TEXT NOT NULL DEFAULT 'in'
                  CHECK(direction IN ('in','out')),
    domain        TEXT NOT NULL,
    op            TEXT NOT NULL
                  CHECK(op IN ('add','modify','delete','refresh')),
    payload_json  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending','applied','error','cancelled')),
    error         TEXT DEFAULT '',
    external_tag  TEXT DEFAULT '',
    ref_external  TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    applied_at    TEXT DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_gl_buf_status ON global_local_buffer_op(status, domain);
CREATE INDEX IF NOT EXISTS idx_gl_buf_ext ON global_local_buffer_op(external_tag, status);

-- 6. PRIVILEGES (famille SECURITY — refus par chemin/kind/agent/team/niveau)
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

-- 7. SECURITY_SUPERVISOR (agents superviseurs pour ask=security_supervisor)
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
