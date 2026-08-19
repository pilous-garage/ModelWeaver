-- batch_schema.sql — agrégations d'usage et séquences

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_history_1m (
    bucket INTEGER NOT NULL,
    provider_ref TEXT,
    model_ref TEXT,
    adresse_id INTEGER,
    agent_id TEXT,
    requests INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_thinking INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    req_total INTEGER DEFAULT 0,
    tok_total INTEGER DEFAULT 0,
    first_call INTEGER,
    last_call INTEGER,
    UNIQUE(bucket, provider_ref, model_ref, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_uh1m_bucket ON usage_history_1m(bucket);

CREATE TABLE IF NOT EXISTS usage_history_15m (
    bucket INTEGER NOT NULL,
    provider_ref TEXT,
    model_ref TEXT,
    adresse_id INTEGER,
    agent_id TEXT,
    requests INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_thinking INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    req_total INTEGER DEFAULT 0,
    tok_total INTEGER DEFAULT 0,
    first_call INTEGER,
    last_call INTEGER,
    UNIQUE(bucket, provider_ref, model_ref, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_uh15m_bucket ON usage_history_15m(bucket);

CREATE TABLE IF NOT EXISTS usage_history_3h (
    bucket INTEGER NOT NULL,
    provider_ref TEXT,
    model_ref TEXT,
    adresse_id INTEGER,
    agent_id TEXT,
    requests INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_thinking INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    req_total INTEGER DEFAULT 0,
    tok_total INTEGER DEFAULT 0,
    first_call INTEGER,
    last_call INTEGER,
    UNIQUE(bucket, provider_ref, model_ref, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_uh3h_bucket ON usage_history_3h(bucket);

CREATE TABLE IF NOT EXISTS usage_history_1d (
    bucket INTEGER NOT NULL,
    provider_ref TEXT,
    model_ref TEXT,
    adresse_id INTEGER,
    agent_id TEXT,
    requests INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_thinking INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    req_total INTEGER DEFAULT 0,
    tok_total INTEGER DEFAULT 0,
    first_call INTEGER,
    last_call INTEGER,
    UNIQUE(bucket, provider_ref, model_ref, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_uh1d_bucket ON usage_history_1d(bucket);

CREATE TABLE IF NOT EXISTS usage_history_1w (
    bucket INTEGER NOT NULL,
    provider_ref TEXT,
    model_ref TEXT,
    adresse_id INTEGER,
    agent_id TEXT,
    requests INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_thinking INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    req_total INTEGER DEFAULT 0,
    tok_total INTEGER DEFAULT 0,
    first_call INTEGER,
    last_call INTEGER,
    UNIQUE(bucket, provider_ref, model_ref, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_uh1w_bucket ON usage_history_1w(bucket);

CREATE TABLE IF NOT EXISTS usage_history_1mo (
    bucket INTEGER NOT NULL,
    provider_ref TEXT,
    model_ref TEXT,
    adresse_id INTEGER,
    agent_id TEXT,
    requests INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_thinking INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    req_total INTEGER DEFAULT 0,
    tok_total INTEGER DEFAULT 0,
    first_call INTEGER,
    last_call INTEGER,
    UNIQUE(bucket, provider_ref, model_ref, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_uh1mo_bucket ON usage_history_1mo(bucket);

CREATE TABLE IF NOT EXISTS model_sequence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_ref TEXT NOT NULL,
    model_ref TEXT NOT NULL,
    adresse_id INTEGER,
    seq_type TEXT NOT NULL CHECK(seq_type IN ('success','failure')),
    seq_start INTEGER NOT NULL,
    seq_end INTEGER,
    requests INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    avg_latency_ms REAL DEFAULT 0,
    error_code TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
    updated_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(provider_ref, model_ref, adresse_id, seq_type, seq_start)
);
CREATE INDEX IF NOT EXISTS idx_mseq_model ON model_sequence(provider_ref, model_ref);
CREATE INDEX IF NOT EXISTS idx_mseq_status ON model_sequence(status);
CREATE INDEX IF NOT EXISTS idx_mseq_type ON model_sequence(seq_type);
CREATE INDEX IF NOT EXISTS idx_mseq_start ON model_sequence(seq_start);

CREATE TABLE IF NOT EXISTS llm_caller_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_id TEXT NOT NULL,
    provider_ref TEXT,
    model_ref TEXT,
    adresse_id INTEGER,
    seq_start INTEGER NOT NULL,
    seq_end INTEGER,
    duration_s INTEGER,
    requests INTEGER DEFAULT 0,
    errors INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    avg_latency_ms REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_cs_caller ON llm_caller_sessions(caller_id);
CREATE INDEX IF NOT EXISTS idx_cs_status ON llm_caller_sessions(status);
CREATE INDEX IF NOT EXISTS idx_cs_open ON llm_caller_sessions(caller_id, status);

CREATE TABLE IF NOT EXISTS archive_processing_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    processed_at INTEGER DEFAULT (strftime('%s','now')),
    lines_read INTEGER DEFAULT 0,
    warnings TEXT DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_apr_time ON archive_processing_report(processed_at);
