-- runtime_llm_schema.sql — tables volatiles du bridge LLM
-- Domaine runtime_llm : écriture par ligne per-call, lecture par batcheur

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS adresse_runtime (
    adresse_id INTEGER PRIMARY KEY,
    available INTEGER NOT NULL DEFAULT 1,
    backoff_until INTEGER DEFAULT 0,
    error_since INTEGER DEFAULT 0,
    first_use INTEGER DEFAULT 0,
    last_use INTEGER DEFAULT 0,
    first_respond INTEGER DEFAULT 0,
    last_respond INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS model_call_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    adresse_id INTEGER,
    provider_ref TEXT,
    model_ref TEXT,
    agent_id TEXT,
    caller_id TEXT,
    call_type TEXT DEFAULT 'chat',
    success INTEGER NOT NULL DEFAULT 1,
    key_ref TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_thinking INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    status TEXT CHECK(status IN ('ok','error','rate_limited','quota_exhausted')),
    error_code TEXT,
    error_detail TEXT,
    error_msg TEXT,
    provider_model_id INTEGER,
    meta_json TEXT,
    task_id INTEGER,
    sub_task_id INTEGER,
    sent_at INTEGER NOT NULL,
    received_at INTEGER,
    created_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS model_call_log_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    adresse_id INTEGER,
    provider_ref TEXT,
    model_ref TEXT,
    agent_id TEXT,
    caller_id TEXT,
    call_type TEXT DEFAULT 'chat',
    success INTEGER NOT NULL DEFAULT 1,
    key_ref TEXT,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_thinking INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    status TEXT,
    error_code TEXT,
    error_detail TEXT,
    error_msg TEXT,
    provider_model_id INTEGER,
    meta_json TEXT,
    task_id INTEGER,
    sub_task_id INTEGER,
    sent_at INTEGER NOT NULL,
    received_at INTEGER,
    created_at INTEGER DEFAULT (strftime('%s','now')),
    archived_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS real_call_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_ref TEXT,
    endpoint_id INTEGER,
    key_ref TEXT,
    model_ref TEXT,
    adresse_id INTEGER,
    agent_id TEXT,
    caller_id TEXT,
    sent_at INTEGER NOT NULL,
    received_at INTEGER,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_thinking INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    status TEXT CHECK(status IN ('ok','rate_limited','error','quota_exhausted')),
    error_code TEXT,
    error_detail TEXT,
    window_key TEXT,
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_rcm_model ON real_call_models(model_ref);
CREATE INDEX IF NOT EXISTS idx_rcm_sent ON real_call_models(sent_at);
CREATE INDEX IF NOT EXISTS idx_rcm_status ON real_call_models(status);
CREATE INDEX IF NOT EXISTS idx_rcm_caller ON real_call_models(caller_id);

CREATE TABLE IF NOT EXISTS really_used_budget (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    budget_ref TEXT NOT NULL,
    model_ref TEXT,
    provider_ref TEXT,
    cost REAL DEFAULT 0,
    tokens INTEGER DEFAULT 0,
    usage_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(budget_ref, model_ref, provider_ref, usage_at)
);

CREATE TABLE IF NOT EXISTS budget_consumption (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    budget_ref TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_ref TEXT NOT NULL,
    cost REAL DEFAULT 0,
    tokens INTEGER DEFAULT 0,
    window_start INTEGER,
    window_end INTEGER,
    updated_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(budget_ref, target_type, target_ref, window_start)
);

CREATE TABLE IF NOT EXISTS budget_final (
    budget_ref TEXT PRIMARY KEY,
    limit_cost REAL DEFAULT 0,
    used_cost REAL DEFAULT 0,
    limit_tokens INTEGER DEFAULT 0,
    used_tokens INTEGER DEFAULT 0,
    reset_at INTEGER,
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS agent_budget_allocation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    budget_ref TEXT NOT NULL,
    allocation_cost REAL DEFAULT 0,
    allocation_tokens INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(agent_id, budget_ref)
);

CREATE TABLE IF NOT EXISTS adress_error_state (
    adresse_id INTEGER PRIMARY KEY,
    error_count INTEGER DEFAULT 0,
    last_error_at INTEGER DEFAULT 0,
    last_error_code TEXT,
    last_error_detail TEXT,
    degraded INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS budget_error_state (
    budget_ref TEXT PRIMARY KEY,
    error_count INTEGER DEFAULT 0,
    last_error_at INTEGER DEFAULT 0,
    last_error_code TEXT,
    last_error_detail TEXT,
    degraded INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS model_efficacy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_ref TEXT NOT NULL,
    provider_ref TEXT,
    use_case TEXT DEFAULT 'general',
    score_quality REAL DEFAULT 0,
    score_speed REAL DEFAULT 0,
    score_cost REAL DEFAULT 0,
    score_reliability REAL DEFAULT 0,
    samples INTEGER DEFAULT 0,
    last_evaluated_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(model_ref, provider_ref, use_case)
);

CREATE TABLE IF NOT EXISTS endpoint_model_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id INTEGER,
    model_ref TEXT,
    adresse_id INTEGER,
    agent_id TEXT,
    requests INTEGER DEFAULT 0,
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    tokens_thinking INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    last_call_at INTEGER DEFAULT (strftime('%s','now')),
    last_call_working INTEGER DEFAULT 1,
    error_count INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(endpoint_id, model_ref, adresse_id)
);

-- Index
CREATE INDEX IF NOT EXISTS idx_call_log_sent ON model_call_log(sent_at);
CREATE INDEX IF NOT EXISTS idx_call_log_created ON model_call_log(created_at, id);
CREATE INDEX IF NOT EXISTS idx_call_log_model ON model_call_log(model_ref, provider_ref);
CREATE INDEX IF NOT EXISTS idx_call_log_status ON model_call_log(status);
CREATE INDEX IF NOT EXISTS idx_call_log_caller ON model_call_log(caller_id);
CREATE INDEX IF NOT EXISTS idx_archive_created ON model_call_log_archive(created_at, id);
CREATE INDEX IF NOT EXISTS idx_archive_caller ON model_call_log_archive(caller_id);
CREATE INDEX IF NOT EXISTS idx_budget_consumption_budget ON budget_consumption(budget_ref);
CREATE INDEX IF NOT EXISTS idx_model_efficacy_model ON model_efficacy(model_ref);
CREATE INDEX IF NOT EXISTS idx_endpoint_usage_model ON endpoint_model_usage(model_ref);
