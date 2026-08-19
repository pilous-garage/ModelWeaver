-- security_schema.sql — domaine security.db (autorisations filesystem par agent).

CREATE TABLE IF NOT EXISTS agent_fs_auth (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id INTEGER NOT NULL,
  root_path TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'r',
  UNIQUE(agent_id, root_path));
