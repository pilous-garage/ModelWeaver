-- ============================================================
-- CATALOGUE_GENERE.DB — Domaine des données GÉNÉRÉES (V3 — quad simple).
--
-- PRINCIPE (V3) :
--   - id_data = INTEGER (uint) = hash stable(path_data) — identifiant simple
--     à manipuler (collision détectée/résolue par salt en 1 retry max).
--   - path_data = "kind:scope:name" (ex. "symbol:taskflow.py:decoupe") — le
--     chemin d'accès lisible, UNIQUE par (project_id, path_data).
--   - last_modify = époque ns (mtime max des dépendances / dernière regen) —
--     dirty-flag pour 304 + invalidation.
--   - genere = cache local CALCULÉ (source implicite 'user' = recompute) ;
--     pas de quad source/version — path_data est l'identité.
--   - Backups : backup_of_id (int id_data de l'original), path_data @snap_<ts>.
--   RESET complet (V2 id_data-texte → V3 id_data-int) : re-génération depuis
--   les fichiers (cache recompute, OK).
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '0'
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '3');

CREATE TABLE IF NOT EXISTS questions (
    id_question INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS gen_data (
    project_id      INTEGER NOT NULL,
    id_data         INTEGER NOT NULL,        -- uint = hash(path_data)
    path_data       TEXT NOT NULL,           -- "kind:scope:name"
    name            TEXT NOT NULL,
    kind            TEXT NOT NULL,
    ref_id          TEXT DEFAULT '',
    value           TEXT DEFAULT '',
    value_is_file   INTEGER NOT NULL DEFAULT 0,
    dependencies_json TEXT DEFAULT '[]',
    inputs_hash     TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'valid'
                    CHECK(status IN ('valid','stale','error')),
    questionned     INTEGER DEFAULT NULL,
    backup_of_id    INTEGER,                 -- id_data de l'original (sinon NULL)
    backup_reason   TEXT DEFAULT '',
    generation_mode TEXT NOT NULL DEFAULT 'deterministic'
                    CHECK(generation_mode IN ('deterministic','stochastic')),
    storage         TEXT NOT NULL DEFAULT 'disk'
                    CHECK(storage IN ('ram','disk')),
    last_access_at  REAL DEFAULT 0,           -- epoch ns (access)
    nb_access       INTEGER NOT NULL DEFAULT 0,
    generated_at    REAL DEFAULT 0,           -- epoch ns (création)
    updated_at      REAL DEFAULT 0,           -- epoch ns (dernière modif)
    last_modify     REAL DEFAULT 0,           -- epoch ns (mtime max des deps / dirty-flag)
    PRIMARY KEY (project_id, id_data),
    UNIQUE (project_id, path_data),
    FOREIGN KEY (questionned) REFERENCES questions(id_question) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_gen_kind  ON gen_data(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_gen_path  ON gen_data(project_id, path_data);
CREATE INDEX IF NOT EXISTS idx_gen_status ON gen_data(project_id, status);
    CREATE INDEX IF NOT EXISTS idx_gen_question ON gen_data(questionned) WHERE questionned IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_gen_backup ON gen_data(project_id, backup_of_id);
CREATE INDEX IF NOT EXISTS idx_gen_access ON gen_data(project_id, last_access_at);

CREATE TABLE IF NOT EXISTS gen_dependance (
    project_id      INTEGER NOT NULL,
    data_ref        INTEGER NOT NULL,        -- id_data (int) de la data_genere
    dep_ref         TEXT NOT NULL,           -- dépendance : "symbol:..." / "file:..." (string)
    dep_version     TEXT NOT NULL DEFAULT '',
    role            TEXT NOT NULL DEFAULT 'implementation'
                    CHECK(role IN ('generator','implementation','schema',
                                   'import','reference','member','agent','team')),
    created_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, data_ref, dep_ref, dep_version)
);
CREATE INDEX IF NOT EXISTS idx_gendep_data  ON gen_dependance(project_id, data_ref);
CREATE INDEX IF NOT EXISTS idx_gendep_dep   ON gen_dependance(project_id, dep_ref);
CREATE INDEX IF NOT EXISTS idx_gendep_role  ON gen_dependance(project_id, role);

CREATE TABLE IF NOT EXISTS gen_runs (
    project_id     INTEGER NOT NULL,
    data_ref       INTEGER NOT NULL,         -- id_data (int)
    run_seq        INTEGER NOT NULL,
    value          TEXT DEFAULT '',
    value_is_file  INTEGER NOT NULL DEFAULT 0,
    inputs_hash    TEXT DEFAULT '',
    error          TEXT DEFAULT '',
    created_at     REAL DEFAULT 0,
    PRIMARY KEY (project_id, data_ref, run_seq)
);
CREATE INDEX IF NOT EXISTS idx_genruns_data ON gen_runs(project_id, data_ref);

CREATE TABLE IF NOT EXISTS gen_config (
    project_id   INTEGER NOT NULL DEFAULT 0,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,
    PRIMARY KEY (project_id, key)
);
INSERT OR IGNORE INTO gen_config (project_id, key, value) VALUES
    (0, 'flush_interval_s', '60'),
    (0, 'ram_min_age_s',    '300'),
    (0, 'ram_max_lines',    '5000'),
    (0, 'runs_max',         '10'),
    (0, 'disk_max_age_s',   '2592000'),
    (0, 'stale_check_s',    '60');

CREATE TABLE IF NOT EXISTS gen_fichier (
    project_id       INTEGER NOT NULL,
    ref_file         TEXT NOT NULL,
    last_modif_ts    REAL DEFAULT 0,
    last_hash_ts     REAL DEFAULT 0,
    last_hash        TEXT DEFAULT '',
    PRIMARY KEY (project_id, ref_file)
);
CREATE INDEX IF NOT EXISTS idx_genfichier_ref ON gen_fichier(project_id, ref_file);

CREATE TABLE IF NOT EXISTS gen_runtime_files (
    project_id      INTEGER NOT NULL,
    data_ref        INTEGER NOT NULL,        -- id_data (int)
    glob            TEXT NOT NULL,
    last_read_mtime REAL DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, data_ref, glob)
);
CREATE INDEX IF NOT EXISTS idx_runtime_data ON gen_runtime_files(project_id, data_ref);

CREATE TABLE IF NOT EXISTS gen_file_rules (
    project_id  INTEGER PRIMARY KEY,
    rules_json  TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT DEFAULT (datetime('now'))
);
INSERT OR IGNORE INTO gen_file_rules (project_id, rules_json) VALUES (0, '{
  "include_ext": [".py", ".c", ".cpp", ".h", ".go", ".rs", ".js", ".ts",
                  ".tsx", ".java", ".rb", ".php", ".sql", ".sh", ".txt",
                  ".md", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg"],
  "exclude_ext": [".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".mp3",
                  ".mp4", ".wav", ".aac", ".woff", ".woff2", ".ttf", ".pdf"],
  "include_dirs": [],
  "exclude_dirs": [".git", ".venv", ".venv-bench", "node_modules",
                   "__pycache__", "target", "dist", "build", ".modelweaver",
                   "oldcode", ".idea", ".vscode", "coverage"],
  "include_files": [],
  "exclude_files": ["package-lock.json", "yarn.lock", "Cargo.lock",
                    "poetry.lock"],
  "include_globs": [],
  "exclude_globs": ["*.min.js", "*.pyc"],
  "gitignore": false
}');
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '3');
