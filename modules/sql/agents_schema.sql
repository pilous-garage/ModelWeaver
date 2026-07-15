-- ============================================================
-- AGENTS.DB — Agent identity + runtime tracking
-- Database séparée de modelweaver.db (domaine distinct)
-- ============================================================

-- ============================================================
-- 1. META — Versioning + signalisation GUI
-- ============================================================
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', 1);

-- ============================================================
-- 2. AGENTS — Identité persistante
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
    agent_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT UNIQUE NOT NULL,
    ref           TEXT UNIQUE NOT NULL,             -- "agent:{name}"
    role_type     TEXT NOT NULL,
    occupation    TEXT NOT NULL DEFAULT 'noncontinue'
                  CHECK(occupation IN ('continue', 'noncontinue', 'disparate')),
    status        TEXT DEFAULT 'INIT'
                  CHECK(status IN ('INIT', 'IDLE', 'RUNNING', 'STOPPED', 'TERMINATED')),
    config_json   TEXT,                              -- personality, context, data, tools, channels, signals
    resources_json TEXT,                             -- {llm, ram: {min, max}, cpu: {min, max}, priority, preemptible}
    variables_json TEXT,                             -- variables internes (mémoire de travail)
    state_json    TEXT,                              -- état FSM courant
    successor_id  INTEGER REFERENCES agents(agent_id),
    created_at    TEXT DEFAULT (datetime('now')),
    last_active_at TEXT                              -- dernière déshydratation
);

CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

-- ============================================================
-- 3. AGENT_RUNTIME — Threads actifs (heartbeat)
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_runtime (
    agent_id      INTEGER PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
    thread_id     TEXT UNIQUE,                       -- identifiant OS du thread
    pid           INTEGER,                           -- process ID
    heartbeat_at  TEXT,                              -- dernier heartbeat
    started_at    TEXT,                              -- début d'hydration
    current_step  TEXT                               -- étape FSM en cours (ou NULL)
);

-- ============================================================
-- 4. AGENT_METRICS — Statistiques d'exécution
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_metrics (
    agent_id        INTEGER PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
    total_tasks     INTEGER DEFAULT 0,
    failed_tasks    INTEGER DEFAULT 0,
    total_tokens    INTEGER DEFAULT 0,
    total_runtime_ms INTEGER DEFAULT 0,
    avg_latency_ms  REAL DEFAULT 0,
    last_updated    TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 5. AGENT_SIGNALS — Canal de supervision parallèle
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_signals (
    signal_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    type            TEXT NOT NULL
                    CHECK(type IN ('pause', 'resume', 'status', 'health', 'kill', 'configure')),
    payload_json    TEXT,
    status          TEXT DEFAULT 'PENDING'
                    CHECK(status IN ('PENDING', 'ACKED', 'COMPLETED', 'FAILED')),
    created_at      TEXT DEFAULT (datetime('now')),
    acknowledged_at TEXT,
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_agent_status ON agent_signals(agent_id, status);
