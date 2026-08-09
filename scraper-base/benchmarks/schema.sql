-- ============================================================
-- model_benchmarks_raw — Données brutes scrapees par source.
-- Chaque ligne = un score d'un modele sur un benchmark a un instant T.
-- Les sources sont conservees individuellement pour traçabilite.
-- ============================================================
CREATE TABLE IF NOT EXISTS model_benchmarks_raw (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_ref       TEXT NOT NULL,          -- 'openai/gpt-4o'
    benchmark_key   TEXT NOT NULL,          -- 'lmsys_arena', 'arena_hard_auto', 'synthetic_catalogue'
    metric_name     TEXT NOT NULL DEFAULT 'score',
    raw_value       REAL NOT NULL,          -- valeur brute
    percentile      REAL,                   -- 0-100, normalise par rapport aux autres modeles
    source_url      TEXT DEFAULT '',
    fetched_at       TEXT DEFAULT (datetime('now')),
    is_synthetic    INTEGER DEFAULT 0,      -- 1 = data estimée, 0 = donnée réelle
    confidence      REAL DEFAULT 1.0,       -- 0.0-1.0, confiance de la source
    meta_json       TEXT DEFAULT '{}',      -- métadonnées source (release_date, creator…)
    PRIMARY KEY (model_ref, benchmark_key, metric_name)
);

-- ============================================================
-- model_efficacy — Scores consolidés par modèle.
-- Chaque ligne = un modèle, avec scores par domaine de tâche.
-- Les colonnes de task-type (score_coding, score_reasoning, etc.)
-- sont peuplées uniquement quand la source de benchmark le permet.
-- ============================================================
CREATE TABLE IF NOT EXISTS model_efficacy (
    model_id            INTEGER PRIMARY KEY,
    use_case            TEXT DEFAULT 'general',
    score_quality       REAL DEFAULT 0,
    score_speed         REAL DEFAULT 0,
    score_cost          REAL DEFAULT 50,
    score_reliability   REAL DEFAULT 50,
    score_chat          REAL DEFAULT 0,
    score_knowledge     REAL DEFAULT 0,
    score_coding        REAL DEFAULT 0,
    score_reasoning     REAL DEFAULT 0,
    score_agentic       REAL DEFAULT 0,
    global_score        REAL DEFAULT 0,
    samples             INTEGER DEFAULT 0,
    source_count        INTEGER DEFAULT 0,
    is_synthetic        INTEGER DEFAULT 1,
    benchmark_keys      TEXT DEFAULT '[]',
    model_ref           TEXT,
    FOREIGN KEY (model_id) REFERENCES catalogue_models(id)
);

-- ============================================================
-- benchmark_scrape_log — Historique des scrapes.
-- ============================================================
CREATE TABLE IF NOT EXISTS benchmark_scrape_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name     TEXT NOT NULL,
    source_url      TEXT DEFAULT '',
    rows_fetched    INTEGER DEFAULT 0,
    completed_at    TEXT DEFAULT (datetime('now')),
    success         INTEGER DEFAULT 1,
    error_msg       TEXT DEFAULT ''
);