-- score_schema.sql — scores, buckets, benchmarks

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS score_thinking_power (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id INTEGER NOT NULL,
    score_thinking REAL DEFAULT 0,
    samples INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(model_id)
);
CREATE INDEX IF NOT EXISTS idx_stp_model ON score_thinking_power(model_id);

CREATE TABLE IF NOT EXISTS model_bucket_counts (
    provider_ref TEXT NOT NULL,
    model_ref TEXT NOT NULL,
    adresse_id INTEGER,
    s5_succ_0 INTEGER DEFAULT 0, s5_tot_0 INTEGER DEFAULT 0,
    s5_succ_1 INTEGER DEFAULT 0, s5_tot_1 INTEGER DEFAULT 0,
    s5_succ_2 INTEGER DEFAULT 0, s5_tot_2 INTEGER DEFAULT 0,
    s5_succ_3 INTEGER DEFAULT 0, s5_tot_3 INTEGER DEFAULT 0,
    s5_succ_4 INTEGER DEFAULT 0, s5_tot_4 INTEGER DEFAULT 0,
    s5_succ_5 INTEGER DEFAULT 0, s5_tot_5 INTEGER DEFAULT 0,
    s5_succ_6 INTEGER DEFAULT 0, s5_tot_6 INTEGER DEFAULT 0,
    s5_succ_7 INTEGER DEFAULT 0, s5_tot_7 INTEGER DEFAULT 0,
    s5_succ_8 INTEGER DEFAULT 0, s5_tot_8 INTEGER DEFAULT 0,
    s5_succ_9 INTEGER DEFAULT 0, s5_tot_9 INTEGER DEFAULT 0,
    s5_succ_10 INTEGER DEFAULT 0, s5_tot_10 INTEGER DEFAULT 0,
    s5_succ_11 INTEGER DEFAULT 0, s5_tot_11 INTEGER DEFAULT 0,
    sh_succ_0 INTEGER DEFAULT 0, sh_tot_0 INTEGER DEFAULT 0,
    sh_succ_1 INTEGER DEFAULT 0, sh_tot_1 INTEGER DEFAULT 0,
    sh_succ_2 INTEGER DEFAULT 0, sh_tot_2 INTEGER DEFAULT 0,
    sh_succ_3 INTEGER DEFAULT 0, sh_tot_3 INTEGER DEFAULT 0,
    sh_succ_4 INTEGER DEFAULT 0, sh_tot_4 INTEGER DEFAULT 0,
    sh_succ_5 INTEGER DEFAULT 0, sh_tot_5 INTEGER DEFAULT 0,
    sh_succ_6 INTEGER DEFAULT 0, sh_tot_6 INTEGER DEFAULT 0,
    sh_succ_7 INTEGER DEFAULT 0, sh_tot_7 INTEGER DEFAULT 0,
    sh_succ_8 INTEGER DEFAULT 0, sh_tot_8 INTEGER DEFAULT 0,
    sh_succ_9 INTEGER DEFAULT 0, sh_tot_9 INTEGER DEFAULT 0,
    sh_succ_10 INTEGER DEFAULT 0, sh_tot_10 INTEGER DEFAULT 0,
    sh_succ_11 INTEGER DEFAULT 0, sh_tot_11 INTEGER DEFAULT 0,
    sh_succ_12 INTEGER DEFAULT 0, sh_tot_12 INTEGER DEFAULT 0,
    sh_succ_13 INTEGER DEFAULT 0, sh_tot_13 INTEGER DEFAULT 0,
    sh_succ_14 INTEGER DEFAULT 0, sh_tot_14 INTEGER DEFAULT 0,
    sh_succ_15 INTEGER DEFAULT 0, sh_tot_15 INTEGER DEFAULT 0,
    sh_succ_16 INTEGER DEFAULT 0, sh_tot_16 INTEGER DEFAULT 0,
    sh_succ_17 INTEGER DEFAULT 0, sh_tot_17 INTEGER DEFAULT 0,
    sh_succ_18 INTEGER DEFAULT 0, sh_tot_18 INTEGER DEFAULT 0,
    sh_succ_19 INTEGER DEFAULT 0, sh_tot_19 INTEGER DEFAULT 0,
    sh_succ_20 INTEGER DEFAULT 0, sh_tot_20 INTEGER DEFAULT 0,
    sh_succ_21 INTEGER DEFAULT 0, sh_tot_21 INTEGER DEFAULT 0,
    sh_succ_22 INTEGER DEFAULT 0, sh_tot_22 INTEGER DEFAULT 0,
    sd_succ_0 INTEGER DEFAULT 0, sd_tot_0 INTEGER DEFAULT 0,
    sd_succ_1 INTEGER DEFAULT 0, sd_tot_1 INTEGER DEFAULT 0,
    sd_succ_2 INTEGER DEFAULT 0, sd_tot_2 INTEGER DEFAULT 0,
    sd_succ_3 INTEGER DEFAULT 0, sd_tot_3 INTEGER DEFAULT 0,
    sd_succ_4 INTEGER DEFAULT 0, sd_tot_4 INTEGER DEFAULT 0,
    sd_succ_5 INTEGER DEFAULT 0, sd_tot_5 INTEGER DEFAULT 0,
    all_time_succ INTEGER DEFAULT 0,
    all_time_tot INTEGER DEFAULT 0,
    score_1h REAL DEFAULT 1.0,
    score_1d REAL DEFAULT 1.0,
    score_1w REAL DEFAULT 1.0,
    score_all REAL DEFAULT 1.0,
    score_last_5 REAL DEFAULT 1.0,
    score_total REAL DEFAULT 4.0,
    updated_at INTEGER DEFAULT (strftime('%s','now')),
    PRIMARY KEY (provider_ref, model_ref)
);

CREATE TABLE IF NOT EXISTS score_bucket_heads (
    bucket_type TEXT PRIMARY KEY,
    last_bucket INTEGER DEFAULT 0,
    timestamp_start INTEGER DEFAULT 0,
    bucket_nb INTEGER DEFAULT 0,
    bucket_qt INTEGER DEFAULT 0,
    bucket_size INTEGER DEFAULT 0
);
INSERT OR IGNORE INTO score_bucket_heads (bucket_type, bucket_qt, bucket_size) VALUES
    ('5m', 12, 300), ('1h', 23, 3600), ('1d', 6, 86400);

CREATE TABLE IF NOT EXISTS score_batch_blocks_5m (
    bucket INTEGER NOT NULL,
    provider_ref TEXT NOT NULL,
    model_ref TEXT NOT NULL,
    adresse_id INTEGER,
    requests INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    total_latency_ms REAL DEFAULT 0,
    UNIQUE(bucket, provider_ref, model_ref)
);
CREATE INDEX IF NOT EXISTS idx_sbb5m_bucket ON score_batch_blocks_5m(bucket);

CREATE TABLE IF NOT EXISTS score_batch_blocks_1h (
    bucket INTEGER NOT NULL,
    provider_ref TEXT NOT NULL,
    model_ref TEXT NOT NULL,
    adresse_id INTEGER,
    requests INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    total_latency_ms REAL DEFAULT 0,
    UNIQUE(bucket, provider_ref, model_ref)
);
CREATE INDEX IF NOT EXISTS idx_sbb1h_bucket ON score_batch_blocks_1h(bucket);

CREATE TABLE IF NOT EXISTS score_batch_blocks_1d (
    bucket INTEGER NOT NULL,
    provider_ref TEXT NOT NULL,
    model_ref TEXT NOT NULL,
    adresse_id INTEGER,
    requests INTEGER DEFAULT 0,
    fail_count INTEGER DEFAULT 0,
    total_latency_ms REAL DEFAULT 0,
    UNIQUE(bucket, provider_ref, model_ref)
);
CREATE INDEX IF NOT EXISTS idx_sbb1d_bucket ON score_batch_blocks_1d(bucket);

CREATE TABLE IF NOT EXISTS score_batch (
    provider_ref TEXT NOT NULL,
    model_ref TEXT NOT NULL,
    adresse_id INTEGER,
    requests_5m INTEGER DEFAULT 0,
    requests_1h INTEGER DEFAULT 0,
    requests_1j INTEGER DEFAULT 0,
    requests_1w INTEGER DEFAULT 0,
    fail_count_5m INTEGER DEFAULT 0,
    fail_count_1h INTEGER DEFAULT 0,
    fail_count_1j INTEGER DEFAULT 0,
    fail_count_1w INTEGER DEFAULT 0,
    total_lat_ms_5m REAL DEFAULT 0,
    total_lat_ms_1h REAL DEFAULT 0,
    total_lat_ms_1j REAL DEFAULT 0,
    total_lat_ms_1w REAL DEFAULT 0,
    fr_5m REAL DEFAULT 0,
    fr_1h REAL DEFAULT 0,
    fr_1j REAL DEFAULT 0,
    fr_1w REAL DEFAULT 0,
    lat_5m_ms REAL DEFAULT 0,
    lat_1h_ms REAL DEFAULT 0,
    lat_1j_ms REAL DEFAULT 0,
    lat_1w_ms REAL DEFAULT 0,
    score_fail_rate REAL DEFAULT 0,
    score_latency REAL DEFAULT 0,
    score_etire REAL DEFAULT 0,
    score_final REAL DEFAULT 0,
    score_last_5 REAL DEFAULT 0,
    score_1h REAL DEFAULT 0,
    score_1d REAL DEFAULT 0,
    score_1w REAL DEFAULT 0,
    score_all REAL DEFAULT 0,
    updated_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(provider_ref, model_ref)
);
CREATE INDEX IF NOT EXISTS idx_sbatch_model ON score_batch(provider_ref, model_ref);

CREATE TABLE IF NOT EXISTS score_benchmark_etire (
    model_ref TEXT PRIMARY KEY,
    score_global REAL DEFAULT 0,
    score_etire REAL DEFAULT 0.1,
    score_etire_chat REAL DEFAULT 0.1,
    score_etire_coding REAL DEFAULT 0.1,
    score_etire_reasoning REAL DEFAULT 0.1,
    score_etire_knowledge REAL DEFAULT 0.1,
    score_etire_agentic REAL DEFAULT 0.1,
    src_chat TEXT NOT NULL DEFAULT 'spec',
    src_coding TEXT NOT NULL DEFAULT 'spec',
    src_reasoning TEXT NOT NULL DEFAULT 'spec',
    src_knowledge TEXT NOT NULL DEFAULT 'spec',
    src_agentic TEXT NOT NULL DEFAULT 'spec',
    is_synthetic INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_sbe_etire ON score_benchmark_etire(score_etire);

CREATE TABLE IF NOT EXISTS score_benchmark_meta (
    column_name TEXT PRIMARY KEY,
    min_value REAL NOT NULL,
    max_value REAL NOT NULL,
    nb_models INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);
