-- ============================================================
-- CATALOGUE_GENERE.DB — Domaine des données GÉNÉRÉES (data_genere).
--
-- PRINCIPE :
--   Une data_genere est CALCULÉE depuis des dépendances (fichiers,
--   skills, symbols, générateurs). Elle n'a pas de date propre : sa
--   fraîcheur est dérivée de ses dépendances (inputs_hash). On ne
--   stocke que le RÉSULTAT + l'empreinte de génération — jamais les
--   sources (les sources vivent dans git / le catalogue vraie data).
--
--   Exemples de data_genere : symbol (fonction/classe/méthode), petri,
--   contract in/out d'un skill, dump d'export…
--
--   Scopage : project_id (0 = modelweaver lui-même, sinon projet
--   utilisateur ou projet agent). Une structure unique sert à tous les
--   projets — isolation par clé, purge par project_id.
--
--   Versionning des fichiers : chaque dépendance pointe vers un commit
--   git ('' = HEAD courant, sinon hash immuable). Un fichier est
--   identifié par son chemin AU MOMENT DU COMMIT (renames supportés).
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================
-- 0. META — Versioning du schéma
-- ============================================================
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '0'
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1');

-- ============================================================
-- 1. QUESTIONS — Signaux d'un analyseur de data_genere.
--    Un générateur/analyseur peut flaguer une data_genere comme
--    "étrange" (questionned) : à revérifier, analyser pourquoi elle
--    est étrange, etc. Table simple : id + question en texte.
-- ============================================================
CREATE TABLE IF NOT EXISTS questions (
    id_question INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 2. GEN_DATA — Le data_genere lui-même (UNE table pour tous les
--    kinds : symbol, petri, contract, export…).
--    value : résultat calculé — soit stocké directement (string),
--    soit un fichier_ref (chemin d'un fichier produit, pour les gros
--    résultats — ex. PNG de pétri). value_is_file=1 dans ce cas.
-- ============================================================
CREATE TABLE IF NOT EXISTS gen_data (
    project_id      INTEGER NOT NULL,          -- 0 = modelweaver
    id_data         TEXT NOT NULL,             -- "symbol:taskflow.py:decoupe" | "petri:llm-code"
    name            TEXT NOT NULL,             -- nom court (ex. decoupe)
    kind            TEXT NOT NULL,             -- symbol | petri | contract | export | …
    path            TEXT DEFAULT '',           -- scopage par chemin : "taskflow.py:decoupe"
    ref_id          TEXT DEFAULT '',           -- scopage par id_data référent (ex. fichier_ref, symbol_id)
    value           TEXT DEFAULT '',           -- résultat : string directe OU fichier_ref
    value_is_file   INTEGER NOT NULL DEFAULT 0, -- 1 = value est un chemin de fichier
    dependencies_json TEXT DEFAULT '[]',       -- graphe d'invalidation (JSON) : [{dep_ref, version, hash}]
    inputs_hash     TEXT DEFAULT '',           -- empreinte des entrées de génération (invalidation)
    status          TEXT NOT NULL DEFAULT 'valid'
                    CHECK(status IN ('valid','stale','error')),
    questionned     INTEGER DEFAULT NULL,      -- → questions(id_question) ; NULL = non signalé
    -- BACKUP LOG : une copie validée de la data (avant un changement).
    -- id_data = id_data original + suffixe de backup (ex. "petri:llm-code@bak_<ts>").
    -- On duplique value + dépendances + inputs_hash : le backup reste LISIBLE
    -- et re-vérifiable tel qu'il était, même si l'original est re-généré.
    backup_of       TEXT DEFAULT '',           -- id_data original ('' = pas un backup)
    backup_reason   TEXT DEFAULT '',           -- pourquoi ce backup (ex. "avant modif règles")
    generation_mode TEXT NOT NULL DEFAULT 'deterministic'
                    CHECK(generation_mode IN ('deterministic','stochastic')),
    storage         TEXT NOT NULL DEFAULT 'disk'
                    CHECK(storage IN ('ram','disk')),
    last_access_at  TEXT,                      -- pour l'éviction RAM / videurs disque
    nb_access       INTEGER NOT NULL DEFAULT 0,
    generated_at    TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, id_data),
    FOREIGN KEY (questionned) REFERENCES questions(id_question) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_gen_kind       ON gen_data(project_id, kind);
CREATE INDEX IF NOT EXISTS idx_gen_path       ON gen_data(project_id, path);
CREATE INDEX IF NOT EXISTS idx_gen_status     ON gen_data(project_id, status);
CREATE INDEX IF NOT EXISTS idx_gen_question   ON gen_data(questionned) WHERE questionned IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_gen_backup     ON gen_data(project_id, backup_of);
CREATE INDEX IF NOT EXISTS idx_gen_access     ON gen_data(project_id, last_access_at);

-- ============================================================
-- 3. GEN_DEPENDANCE — Graphe dirigé data_genere → dépendances.
--    Permet de requêter le graphe (remonter "qui dépend de X ?",
--    partager des sous-graphes). dep_version : '' = HEAD, sinon hash
--    git figé (immuable). Le GÉNÉRATEUR lui-même est une dépendance
--    (role='generator') : changer de traducteur → invalidation.
-- ============================================================
CREATE TABLE IF NOT EXISTS gen_dependance (
    project_id   INTEGER NOT NULL,
    data_ref     TEXT NOT NULL,                -- id_data de la data_genere
    dep_ref      TEXT NOT NULL,                -- dépendance : symbol / fichier / generator
    dep_version  TEXT NOT NULL DEFAULT '',     -- '' = HEAD (suivi), sinon hash git
    role         TEXT NOT NULL DEFAULT 'implementation'
                 CHECK(role IN ('generator','implementation','schema','import','reference','member','agent','team')),
    created_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, data_ref, dep_ref, dep_version)
);

CREATE INDEX IF NOT EXISTS idx_gendep_data   ON gen_dependance(project_id, data_ref);
CREATE INDEX IF NOT EXISTS idx_gendep_dep    ON gen_dependance(project_id, dep_ref);
CREATE INDEX IF NOT EXISTS idx_gendep_role   ON gen_dependance(project_id, role);

-- ============================================================
-- 4. GEN_RUNS — Historique des générations NON déterministes
--    (stochastic : LLM, simulations…). Ring buffer par data :
--    on garde les max_runs plus récents (10 par défaut,
--    paramétrable) — à l'insertion au-delà, on RÉÉCRIT le plus
--    vieux. Chaque run garde sa valeur + ses entrées + l'erreur
--    éventuelle (un run peut échouer alors qu'un autre passe).
-- ============================================================
CREATE TABLE IF NOT EXISTS gen_runs (
    project_id      INTEGER NOT NULL,
    data_ref        TEXT NOT NULL,             -- → gen_data.id_data
    run_seq         INTEGER NOT NULL,          -- ring : 1..max_runs (réécrit)
    value           TEXT DEFAULT '',
    value_is_file   INTEGER NOT NULL DEFAULT 0,
    inputs_hash     TEXT DEFAULT '',
    error           TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, data_ref, run_seq)
);

CREATE INDEX IF NOT EXISTS idx_genruns_data ON gen_runs(project_id, data_ref);

-- ============================================================
-- 5. GEN_CONFIG — Paramètres du moteur (RAM/disque, éviction).
--    Stocké dans le domaine pour persister entre redémarrages.
-- ============================================================
CREATE TABLE IF NOT EXISTS gen_config (
    project_id   INTEGER NOT NULL DEFAULT 0,
    key          TEXT NOT NULL,                -- flush_interval_s, ram_min_age_s,
                                               -- ram_max_lines, runs_max, disk_max_age_s
    value        TEXT NOT NULL,
    PRIMARY KEY (project_id, key)
);

INSERT OR IGNORE INTO gen_config (project_id, key, value) VALUES
    (0, 'flush_interval_s', '60'),
    (0, 'ram_min_age_s',    '300'),   -- 5 min par défaut
    (0, 'ram_max_lines',    '5000'),
    (0, 'runs_max',         '10'),
    (0, 'disk_max_age_s',   '2592000'), -- 30 jours (videurs disque)
    (0, 'stale_check_s',    '60');    -- ticker staleness (vérification fichiers)

-- ============================================================
-- 6. GEN_FICHIER — Cache de hash des FICHIERS dépendances.
--    UNE ligne par fichier référencé par une dépendance (pas tout le
--    projet : seuls les fichiers du graphe sont trackés).
--    Le ticker staleness compare last_hash_ts vs last_modif_ts :
--      si last_hash_ts <= last_modif_ts → le fichier a bougé → re-hash.
--    Si le nouveau hash diffère → propagation stale (rebond dans les
--    gen_dependance : marquer stale les data qui dépendent de ce fichier,
--    récursivement sur leurs propres dépendants).
--    last_modif_ts : mtime disque (float, résolution ms) lu au dernier check.
--    last_hash_ts  : horodatage du dernier calcul de hash.
-- ============================================================
CREATE TABLE IF NOT EXISTS gen_fichier (
    project_id       INTEGER NOT NULL,
    ref_file         TEXT NOT NULL,             -- chemin relatif repo
    last_modif_ts    REAL DEFAULT 0,            -- mtime lu au dernier stat
    last_hash_ts     REAL DEFAULT 0,            -- quand on a hashé (unix)
    last_hash        TEXT DEFAULT '',           -- sha1 du contenu (last_hash_ts)
    PRIMARY KEY (project_id, ref_file)
);

CREATE INDEX IF NOT EXISTS idx_genfichier_ref ON gen_fichier(project_id, ref_file);

-- ============================================================
-- 7b. GEN_RUNTIME_FILES — Fichiers runtime/générés (logs, outputs).
--    Fichiers GITIGNORÉS (pas de "vraies" sources) mais ANALYSABLES
--    par les data_genere (ex. analyse all_files *.log).
--    Non surveillés par le file_watcher (mtime) : ils sont vérifiés
--    par VÉRIFICATION INVERSE au get de la data qui les consomme.
--    `glob` : pattern (ex. "logs/*.log") — dépendance DYNAMIQUE, le
--    set de fichiers peut changer. `last_read_mtime` : mtime max du
--    glob au dernier get → si plus récent, la data est stale.
-- ============================================================
CREATE TABLE IF NOT EXISTS gen_runtime_files (
    project_id      INTEGER NOT NULL,
    data_ref        TEXT NOT NULL,        -- data_genere qui lit ces fichiers
    glob            TEXT NOT NULL,        -- "logs/*.log" (dépendance dynamique)
    last_read_mtime REAL DEFAULT 0,       -- mtime max au dernier get (ns)
    created_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, data_ref, glob)
);

CREATE INDEX IF NOT EXISTS idx_runtime_data ON gen_runtime_files(project_id, data_ref);

-- ============================================================
-- 7. GEN_FILE_RULES — Configuration de scan par PROJET.
--    Stockée en JSON (une ligne par projet). Détermine quels
--    fichiers/dossiers le file_watcher surveille.
--    Structure (rules_json) :
--      {
--        "include_ext": [".py", ".c", ".txt", ".md", ".yaml", ".json"],
--        "exclude_ext": [".png", ".mp3"],
--        "include_dirs": ["src", "tests"],
--        "exclude_dirs": ["node_modules", ".git", ".venv", "target", "dist"],
--        "include_files": ["Makefile"],
--        "exclude_files": ["package-lock.json"],
--        "include_globs": ["*.test.py"],
--        "exclude_globs": ["*.min.js"],
--        "gitignore": false        -- recouper .gitignore (explicite, via
--                                  -- les skills commit qui mettent à jour)
--      }
--    Valeurs par défaut seedées pour project_id=0 (modelweaver) :
--      - extensions de LANGAGE (.py, .c, .go, .rs, .js, .ts, .sql...) ;
--      - txt/md, .yaml/.json (config) ;
--      - dossiers générés (npm, build, venv...) et cachés (commençant par .)
--        ignorés par défaut.
-- ============================================================
CREATE TABLE IF NOT EXISTS gen_file_rules (
    project_id  INTEGER PRIMARY KEY,
    rules_json  TEXT NOT NULL DEFAULT '{}',
    updated_at  TEXT DEFAULT (datetime('now'))
);

-- Défaut : extensions de langage + txt/md/yaml/json ; dossiers générés et
-- cachés ('.*') ignorés. Surchargé par projet via set_file_rules().
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
