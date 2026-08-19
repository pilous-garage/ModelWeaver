-- runtime.db — infos runtime du daemon et des services.
--
-- runtime_state    : une ligne (state_id=1) — état global du process
--                    (pid, host, version, boot_at, HOME, python).
-- service_runtime  : par service — pid_service, port_service, status,
--                    LAST_TICK / NEXT_TICK (échéances du ticker, dupliquées
--                    depuis le cache chaud du ticker), nb de ticks partis.
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS runtime_state (
    state_id      INTEGER PRIMARY KEY CHECK (state_id = 1),
    pid           INTEGER DEFAULT 0,
    host          TEXT DEFAULT '',
    mw_version    TEXT DEFAULT '',
    boot_at       TEXT DEFAULT '',
    mw_home       TEXT DEFAULT '',
    python_version TEXT DEFAULT '',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS service_runtime (
    svc_name        TEXT PRIMARY KEY,      -- même nom que services.name
    pid             INTEGER DEFAULT 0,
    port            INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'registered', -- registered | running | stopped | error
    last_tick       REAL DEFAULT 0,        -- epoch (monotonic) du dernier tick
    next_tick       REAL DEFAULT 0,        -- échéance du prochain tick
    last_duration_s REAL DEFAULT 0,
    ticks_count     INTEGER DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);