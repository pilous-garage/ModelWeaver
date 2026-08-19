-- agent_schema.sql — domaine agents.db (identité + runtime des agents).
-- Domaine OUVERT (multi-écrivains). DROP des tables obsolètes (migration v1->v2).

DROP TABLE IF EXISTS team_tasks;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS conversation_messages;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS agents (
  agent_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  ref TEXT UNIQUE NOT NULL,
  role_type TEXT NOT NULL,
  occupation TEXT NOT NULL DEFAULT 'noncontinue'
  CHECK(occupation IN ('continue','noncontinue','disparate')),
  status TEXT DEFAULT 'INIT'
  CHECK(status IN ('INIT','IDLE','RUNNING','STOPPED','TERMINATED')),
  config_json TEXT,
  resources_json TEXT,
  variables_json TEXT,
  state_json TEXT,
  storage_json TEXT,
  home TEXT DEFAULT '',
  successor_id INTEGER REFERENCES agents(agent_id),
  id_proprietaire TEXT,
  id_team INTEGER,
  created_at TEXT DEFAULT (datetime('now')),
  last_active_at TEXT);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

CREATE TABLE IF NOT EXISTS agent_runtime (
  agent_id INTEGER PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
  thread_id TEXT UNIQUE,
  pid INTEGER,
  heartbeat_at TEXT,
  started_at TEXT,
  current_step TEXT,
  id_proprietaire TEXT,
  id_team INTEGER);

CREATE TABLE IF NOT EXISTS agent_metrics (
  agent_id INTEGER PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
  total_tasks INTEGER DEFAULT 0,
  failed_tasks INTEGER DEFAULT 0,
  total_tokens INTEGER DEFAULT 0,
  total_runtime_ms INTEGER DEFAULT 0,
  avg_latency_ms REAL DEFAULT 0,
  last_updated TEXT DEFAULT (datetime('now')));

CREATE TABLE IF NOT EXISTS agent_signals (
  signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id INTEGER NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  payload_json TEXT,
  status TEXT DEFAULT 'PENDING'
  CHECK(status IN ('PENDING','ACKED','COMPLETED','FAILED')),
  created_at TEXT DEFAULT (datetime('now')),
  acknowledged_at TEXT,
  completed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_signals_agent_status
  ON agent_signals(agent_id, status);

CREATE TABLE IF NOT EXISTS wait_for (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id    INTEGER NOT NULL,
  condition   TEXT NOT NULL,   -- JSON {type, workspace_id, role}
  status      TEXT DEFAULT 'waiting',  -- waiting / ready / done
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  ready_at    TEXT,
  expires_at  TEXT);
CREATE INDEX IF NOT EXISTS idx_wait_for_status
  ON wait_for(status, agent_id);

CREATE TABLE IF NOT EXISTS agent_entrypoints (
  entrypoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id INTEGER NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
  ep_name TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  trigger TEXT DEFAULT '',
  step_id TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(agent_id, ep_name));
CREATE INDEX IF NOT EXISTS idx_agent_ep_prio
  ON agent_entrypoints(agent_id, priority);

CREATE TABLE IF NOT EXISTS auth_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL UNIQUE,
  agent_id TEXT NOT NULL,
  team_id TEXT,
  action TEXT NOT NULL,
  target TEXT,
  reason TEXT DEFAULT '',
  scope TEXT DEFAULT 'once',
  approver_level TEXT DEFAULT 'leader',
  request_type TEXT DEFAULT 'pending_leader',
  status TEXT DEFAULT 'pending',
  approver_id TEXT,
  rejection_reason TEXT,
  resolved_scope TEXT,
  created_at REAL,
  resolved_at REAL,
  conversation_id TEXT);
CREATE INDEX IF NOT EXISTS idx_auth_status
  ON auth_requests(status, approver_level);
CREATE INDEX IF NOT EXISTS idx_auth_conv
  ON auth_requests(conversation_id, status);

INSERT OR IGNORE INTO agent_entrypoints (agent_id, ep_name, priority)
  SELECT agent_id, 'main', 0 FROM agents;
INSERT OR IGNORE INTO agent_entrypoints (agent_id, ep_name, priority, trigger)
  SELECT agent_id, 'cancel', 2, 'signal:cancel' FROM agents;
INSERT OR IGNORE INTO agent_entrypoints (agent_id, ep_name, priority, trigger)
  SELECT agent_id, 'pause', 3, 'signal:pause' FROM agents;
