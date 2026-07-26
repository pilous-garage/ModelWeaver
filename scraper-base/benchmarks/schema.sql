-- ============================================================
-- MODEL_BENCHMARKS_RAW — Donnees brutes scrapees par source.
-- Chaque ligne = un score d'un modele sur un benchmark a un instant T.
-- Les sources sont conservees individuellement pour traçabilite.
-- ============================================================
CREATE TABLE IF NOT EXISTS model_benchmarks_raw (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_ref       TEXT NOT NULL,          -- 'openai/gpt-4o'
    benchmark_key   TEXT NOT NULL,          -- 'lmsys_arena_elo', 'artificial_analysis_quality'
    metric_name     TEXT NOT NULL DEFAULT 'score',  -- 'elo', 'quality', 'speed_tps', 'cost_per_m'
    raw_value       REAL NOT NULL,          -- valeur brute (ex: 1286.5 pour Elo)
    percentile      REAL,                   -- 0-100, normalise par rapport aux autres modeles de la meme source
    source_url      TEXT DEFAULT '',
    fetched_at      TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (model_ref, benchmark_key, metric_name)
);
