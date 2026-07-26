-- ============================================================
-- MODEL_BENCHMARKS_RAW — Donnees brutes scrapees par source.
-- Chaque ligne = un score d'un modele sur un benchmark a un instant T.
-- Les sources sont conservees individuellement pour traçabilite.
-- ============================================================
CREATE TABLE IF NOT EXISTS model_benchmarks_raw (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_ref       TEXT NOT NULL,
    benchmark_key   TEXT NOT NULL,
    metric_name     TEXT NOT NULL DEFAULT 'score',
    raw_value       REAL NOT NULL,
    percentile      REAL,
    source_url      TEXT DEFAULT '',
    fetched_at      TEXT DEFAULT (datetime('now')),
    is_synthetic    INTEGER DEFAULT 0,
    confidence      REAL DEFAULT 1.0,
    PRIMARY KEY (model_ref, benchmark_key, metric_name)
);
