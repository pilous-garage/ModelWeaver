-- ============================================================
-- LOCAL_CATALOGUE.DB — Catalogue local des skills/agents/teams/
-- services/tools/llm + namespaces imbriqués.
--
-- PRINCIPE :
--   - Lectures directes pour tout le monde (connexion mode=ro).
--   - Écritures centralisées : UN SEUL writer autorisé (service
--     write_catalogue), garde par token (niveau app) + mode=ro
--     pour les lecteurs (niveau moteur SQLite).
--   - Pattern par TYPE de données : pour chaque type x on crée
--     x_catalogue, x_tags, x_tag_types, x_limites (à chaud).
--   - Namespaces imbriqués : table `namespaces` (arborescence).
-- ============================================================

-- ============================================================
-- 0. META — Versioning
-- ============================================================
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', 1);

-- ============================================================
-- 1. CATALOGUES — Registre des types de données du catalogue.
--    Chaque ligne = un type (agent, skill, service, tool, llm…)
--    dont les tables {type}_catalogue/_tags/_tag_types/_limites
--    ont été créées (à chaud par le writer).
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogues (
    type           TEXT PRIMARY KEY,                -- "agent", "skill"…
    description    TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT DEFAULT (datetime('now')),
    updated_at     TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 2. NAMESPACES — Arborescence des namespaces imbriqués.
--    parent TEXT REFERENCES namespaces(ns). Racine = parent NULL.
--    La résolution runtime "catalogue.a.b.fn" suit le chemin.
-- ============================================================
CREATE TABLE IF NOT EXISTS namespaces (
    ns            TEXT PRIMARY KEY,                 -- "utils", "utils/sort"…
    parent        TEXT REFERENCES namespaces(ns) ON DELETE SET NULL,
    description   TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ns_parent ON namespaces(parent);

-- ============================================================
-- 2b. SHARED_DEFAULT — Valeur par défaut de partage, TOUTES data.
--    Résolution "can_be_shared" en cascade :
--      (data_type, tag_type, tag_value) → (data_type, '*', '*')
--    Tag '*' = appliqué à toutes les entrées du type.
--    can_be_shared ∈ non | everyone | enterprise | friends | official
-- ============================================================
CREATE TABLE IF NOT EXISTS shared_default (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    data_type     TEXT NOT NULL,             -- "agent", "skill"…
    tag_type      TEXT NOT NULL DEFAULT '*',
    tag_value     TEXT NOT NULL DEFAULT '*',
    can_be_shared TEXT NOT NULL DEFAULT 'non'
                CHECK(can_be_shared IN ('non','everyone','enterprise','friends','official')),
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(data_type, tag_type, tag_value)
);

-- ============================================================
-- 2c. LAST_ACCESS / LAST_MODIFY — traces GLOBALES par type.
--    Last_access : JAMAIS d'écriture par read (les lecteurs sont en
--    mode=ro) — les reads BUFFERISENT en mémoire et le WRITER flushe
--    par lots (batch INSERT ... ON CONFLICT). Zéro contention.
--    Last_modify : écrit par le writer seul (à chaque upsert/delete).
-- ============================================================
CREATE TABLE IF NOT EXISTS last_access_data (
    data_type      TEXT NOT NULL,
    ref            TEXT NOT NULL,
    last_access_at TEXT DEFAULT (datetime('now')),
    access_count   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (data_type, ref)
);

CREATE TABLE IF NOT EXISTS last_modify_data (
    data_type      TEXT NOT NULL,
    ref            TEXT NOT NULL,
    last_modify_at TEXT DEFAULT (datetime('now')),
    modify_count   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (data_type, ref)
);

-- ============================================================
-- 2d. CATALOGUE_PATH — Environnement de paths local.
--    ref (dans une entrée) = ADRESSE SYSTÈME, absolue.
--    path (dans une entrée) = SYMBOLIQUE, variable, configurable.
--    Résolution de path par PRÉCISION, de gauche à droite :
--      /lib/a/$1 (x) prime sur /lib/$1/b (y) pour /lib/a/b
--      (le segment littéral `a` est résolu AVANT la variable).
--    Variables nommées $1, $2… substituées à la résolution.
--    Interdictions : path ne commence JAMAIS par /$ ou $ ; les ÉCRITURES
--    de chemin interdisent `*` (lectures seules : agent/*/home).
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_path (
    path_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    path_name    TEXT NOT NULL UNIQUE,       -- "/lib/$1" (symbolique, configurable)
    address      TEXT NOT NULL,              -- "/.modelweaver/lib/$1" (adresse système absolue)
    scheme       TEXT NOT NULL DEFAULT 'file',  -- file | http | …
    description  TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_cp_scheme ON catalogue_path(scheme);

-- ============================================================
-- 2e. PRIVILEGES — Autorisations par chemin (table séparée).
--    chemin_ref : une ref canonique (kind='ref', ex utils/bubble_sort@v1),
--                 un path (kind='path', ex /bin/modelweaver) ou une
--                 COMMANDE (kind='cmd', ex git, docker) — fini les whitelist.
--    agent_id   : -1 = TOUS les agents (défaut global), sinon l'agent ciblé.
--    team       : -1 = TOUTES les teams (défaut), sinon la team ciblée.
--    Chaque colonne (read/write/exec/privileged) = MODE UNIX 4 GROUPES,
--    un groupe par niveau : humain_with_root, humain, agent_with_root, agent.
--    Notation : 'r'|'w'|'x'|'p' présent ou '-' absent (ex read='r-r-').
--    level : IMPORTANCE, 0..MAX_UINT32. MAX_UINT32 = TOUJOURS.
--    deadline : expiration TEMPORELLE (date ISO) ; NULL/'' = jamais.
--    Conditions additionnelles (nb_times, ref_id, until_restart…) dans
--    privilege_conditions (une ligne par condition, intersection).
-- ============================================================
CREATE TABLE IF NOT EXISTS privileges (
    id_auth      INTEGER PRIMARY KEY AUTOINCREMENT,
    chemin_ref   TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'path'
                 CHECK(kind IN ('ref','path','cmd')),
    agent_id     INTEGER NOT NULL DEFAULT -1,   -- -1 = tous les agents
    team         INTEGER NOT NULL DEFAULT -1,   -- -1 = toutes les teams
    level        INTEGER NOT NULL DEFAULT 1,
    read         TEXT NOT NULL DEFAULT '----',   -- 4 groupes (4 chars)
    write        TEXT NOT NULL DEFAULT '----',
    exec         TEXT NOT NULL DEFAULT '----',
    privileged   TEXT NOT NULL DEFAULT '----',
    ask          TEXT NOT NULL DEFAULT 'none',   -- niveau de DEMANDE requis
    deadline     TEXT DEFAULT NULL,              -- date ISO (NULL = jamais)
    description  TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now')),
    CHECK(ask IN ('none','security_supervisor','human','human_root'))
);

CREATE INDEX IF NOT EXISTS idx_priv_kind ON privileges(kind, chemin_ref);
CREATE INDEX IF NOT EXISTS idx_priv_agent ON privileges(agent_id, team);
CREATE INDEX IF NOT EXISTS idx_priv_deadline ON privileges(deadline);

-- ============================================================
-- 2e-bis. PRIVILEGE_CONDITIONS — Conditions par autorisation.
--    Une ligne = une condition (INTERSECTION : toutes doivent être
--    satisfaites pour que l'autorisation soit valide).
--    condition_type :
--      nb_times      → valeur = nombre max d'utilisations ; compteur
--                      décrémenté à CHAQUE vérification positive (modify_use).
--      lastcall      → valeur = intervalle minimum en SECONDES entre deux
--                      usages (ex. 10 = au moins 10 s entre chaque appel).
--                      last_use_at = horodatage du dernier usage (mis à jour
--                      à chaque modify_use).
--      until_restart → valide jusqu'au prochain redémarrage.
--      until_date    → valide jusqu'à une date (valeur = ISO).
--      ref_id        → hérite de la validité de l'autorisation référencée
--                      (id_auth dans valeur) : si elle expire, celle-ci aussi.
-- ============================================================
CREATE TABLE IF NOT EXISTS privilege_conditions (
    condition_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    id_auth        INTEGER NOT NULL REFERENCES privileges(id_auth) ON DELETE CASCADE,
    condition_type TEXT NOT NULL,
    valeur         TEXT,
    compteur       INTEGER DEFAULT NULL,
    last_use_at    TEXT DEFAULT NULL,          -- pour lastcall
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pcond_auth ON privilege_conditions(id_auth);

-- ============================================================
-- 2f. SECURITY_SUPERVISOR — Agent(s) superviseur désigné(s).
--    Pour les demandes ask=security_supervisor : un agent de supervision
--    (et non l'humain) approuve. Passoire vide tant qu'aucun superviseur
--    n'est désigné (une demande security_supervisor sans superviseur →
--    refus propre). scope : 'team' (superviseur d'une team) | 'global'.
-- ============================================================
CREATE TABLE IF NOT EXISTS security_supervisor (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    supervisor_agent_id INTEGER NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'global'
                  CHECK(scope IN ('team','global')),
    team_id       INTEGER DEFAULT NULL,      -- si scope='team'
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sup_scope ON security_supervisor(scope, team_id);

-- ============================================================
-- 2g. BENCH_SCORES — Résultats de benchmarks du swarm (feedback).
--    Un framework externe (Inspect/Promptfoo/OpenCompass) bombarde
--    l'endpoint OpenAI-compatible ; on peut soumettre les scores ici
--    pour alimenter l'amélioration (comme model_efficacy, mais pour
--    le swarm). suite = nom du benchmark (HumanEval, MBPP…).
-- ============================================================
CREATE TABLE IF NOT EXISTS bench_scores (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    suite         TEXT NOT NULL,             -- 'human_eval', 'mbpp', 'custom'
    task          TEXT NOT NULL,             -- identifiant de la tâche
    score         REAL NOT NULL,             -- 0..1
    passed        INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    meta_json     TEXT,                      -- durée, erreurs, détail
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_bench_suite ON bench_scores(suite, task);

-- ============================================================
-- 3. TABLES PAR TYPE — créées à chaud par le writer.
--    Le schéma BASE est identique pour chaque type ; les champs
--    spécifiques vivent dans data_json.
-- ============================================================

-- _catalogue : une entrée = {ref, namespace, name, version, data_json}
-- _tags      : tags normalisés (entry_id, tag_type, tag_value)
-- _tag_types : registre des catégories de tags pour ce type
-- _limites   : limites/quotas par entrée
-- _source_and_sharing : provenance + partage de chaque entrée

-- NOTE : les tables {type}_* sont créées par le writer via
-- write_catalogue.create_type (pas de DDL statique ici).
