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
    storage_json  TEXT,                              -- stockage disque proprio (quota/path/used_bytes)
    home          TEXT DEFAULT '',                   -- home déclaré (sous-agents : hérité du maître)
    successor_id  INTEGER REFERENCES agents(agent_id),
    id_proprietaire TEXT,                            -- uint64 (décimal) : agent propriétaire
                                                   --   MAX_UINT64 = agent humain ; NULL = maître racine
    id_team       INTEGER,                           -- team_id stable (MIN(agent_id) de la team)
    created_at    TEXT DEFAULT (datetime('now')),
    last_active_at TEXT                              -- dernière déshydratation
);

CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

-- ============================================================
-- 3. AGENT_RUNTIME — Threads actifs (heartbeat)
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_runtime (
    agent_id        INTEGER PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
    thread_id       TEXT UNIQUE,                       -- identifiant OS du thread
    pid             INTEGER,                           -- process ID
    heartbeat_at    TEXT,                              -- dernier heartbeat
    started_at      TEXT,                              -- début d'hydration
    current_step    TEXT,                              -- étape FSM en cours (ou NULL)
    id_proprietaire TEXT,                              -- uint64 (décimal) : agent propriétaire
                                                       -- (NULL = maître racine)
    id_team         INTEGER                            -- team_id stable de l'agent
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
    type            TEXT NOT NULL,
    payload_json    TEXT,
    status          TEXT DEFAULT 'PENDING'
                    CHECK(status IN ('PENDING', 'ACKED', 'COMPLETED', 'FAILED')),
    created_at      TEXT DEFAULT (datetime('now')),
    acknowledged_at TEXT,
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_agent_status ON agent_signals(agent_id, status);

-- ============================================================
-- 6. AGENT_ENTRYPOINTS — Points d'entrée pilotés en BDD.
--    Chaque entrypoint a un niveau de priorité. Le FSM, au démarrage
--    d'un run, résout l'entrypoint à lancer : parmi les enabled, celui
--    de PLUS HAUTE priorité dont le trigger est actif (signal/état).
--    Priorités types : pause=3 > cancel=2 > ask_auth/receive_auth=1
--    > main=0 (défaut).
--    `trigger` : condition de déclenchement (signal, état, expression) ;
--    vide = toujours actif (ex. main).
--    `step_id` : step de départ du workflow ('' = auto via _find_entry_point).
-- ============================================================
CREATE TABLE IF NOT EXISTS agent_entrypoints (
    entrypoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      INTEGER NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    ep_name       TEXT NOT NULL,              -- "main", "ask_auth", "pause", "cancel"...
    priority      INTEGER NOT NULL DEFAULT 0, -- plus haut = prioritaire
    enabled       INTEGER NOT NULL DEFAULT 1,
    trigger       TEXT DEFAULT '',            -- signal/état ('' = toujours actif)
    step_id       TEXT DEFAULT '',            -- step de départ ('' = auto)
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, ep_name)
);

CREATE INDEX IF NOT EXISTS idx_agent_ep_prio ON agent_entrypoints(agent_id, priority);

-- Entrypoints par défaut pour TOUS les agents (logique agent, pas skill).
-- main=0 (défaut, trigger vide = toujours actif), cancel=2 (trigger
-- signal:cancel), pause=3 (trigger signal:pause). ask_auth/receive_auth
-- déclarés par les agents qui en ont besoin (priority 1, trigger
-- signal:ask_auth). Les triggers signal:* ne s'activent que si le signal
-- correspondant est présent au démarrage.
INSERT OR IGNORE INTO agent_entrypoints (agent_id, ep_name, priority)
SELECT agent_id, 'main', 0 FROM agents;
INSERT OR IGNORE INTO agent_entrypoints (agent_id, ep_name, priority, trigger)
SELECT agent_id, 'cancel', 2, 'signal:cancel' FROM agents;
INSERT OR IGNORE INTO agent_entrypoints (agent_id, ep_name, priority, trigger)
SELECT agent_id, 'pause', 3, 'signal:pause' FROM agents;
