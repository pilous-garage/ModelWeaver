-- services.db — annuaire des SERVICES de ModelWeaver + état des ticks.
--
-- services          : TOUS les services du système (daemon inclus). Chaque
--                     service a un module path (dotted, même langage que
--                     env.path) par lequel on l'appelle/déclare. `sqlite`
--                     est un service (le moteur de données) : kind='sqlite'.
-- service_ticks     : services à tick éphémère (>1s ; ex. file_watcher 60s,
--                     petri_runtime 10s) — table reprise du ServiceTicker.
-- service_ticks_secondes : services à tick permanent (1s).
-- service_tick_runs : historique des runs (thread_id, durée, statut).
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS services (
    service_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT 'service', -- daemon | supervisor_general |
                -- ticker_general | watch | api | sqlite | service
    module      TEXT DEFAULT '',                 -- dotted path (resolver)
    description TEXT DEFAULT '',
    cmd         TEXT DEFAULT '',
    version     TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'registered', -- registered | running | stopped | error
    pid         INTEGER DEFAULT 0,
    port        INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS service_ticks (
    tick_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    svc_name        TEXT UNIQUE NOT NULL REFERENCES services(name)
                    ON DELETE CASCADE,
    tick_interval_s REAL NOT NULL DEFAULT 60,
    cmd             TEXT DEFAULT '',
    last_launch     REAL DEFAULT 0,
    last_duration_s REAL DEFAULT 0,
    running         INTEGER DEFAULT 0,
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS service_ticks_secondes (
    tick_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    svc_name        TEXT UNIQUE NOT NULL REFERENCES services(name)
                    ON DELETE CASCADE,
    cmd             TEXT DEFAULT '',
    last_launch     REAL DEFAULT 0,
    last_duration_s REAL DEFAULT 0,
    running         INTEGER DEFAULT 0,
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS service_tick_runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    svc_name    TEXT NOT NULL REFERENCES services(name) ON DELETE CASCADE,
    thread_id   INTEGER DEFAULT 0,
    started_at  REAL DEFAULT 0,
    finished_at REAL DEFAULT 0,
    duration_s  REAL DEFAULT 0,
    status      TEXT DEFAULT 'running',  -- running | ok | error | timedout
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tick_runs_name ON service_tick_runs(svc_name);