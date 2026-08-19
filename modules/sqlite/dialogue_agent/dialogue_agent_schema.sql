-- dialogue_agent.db — dialogues (domaine OUVERT, pas de writer dédié).
-- Modèle :
--   • conversation (chatroom) : arborescente (parent_conv_id = fork de fils),
--     par projet/team — les dialogues humain↔agent passent par la MÊME
--     arborescence (agent_id NÉGATIF = point de terminaison humain).
--   • direct_message : échanges 1:1 courts entre agents (kind request/reply/
--     info/broadcast…), flushables via flush_tick (limit/âge/condition/
--     archive). `interruption` (none/entrypoint) : les message d'interruption
--     seront gérés par le FSM (plus tard).
--   • humain_as_agent : points de terminaison humains (agent_id NÉGATIF),
--     multi-humains possibles dans une discussion.
--   • human_choice : escalade humain (décision ponctuelle, asynchrone) —
--     liée à une task/sub_task (plus d'issues).
--   • question/reponse : consensus agent→agent (vote.

CREATE TABLE IF NOT EXISTS humain_as_agent (
    agent_id     INTEGER PRIMARY KEY,        -- NÉGATIF (ex. -1, -2, …)
    endpoint     TEXT NOT NULL,              -- entry_chat, cli, api/session…
    label        TEXT DEFAULT '',
    last_seen_at TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversation (
    conv_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT NOT NULL,
    team_id        INTEGER NOT NULL DEFAULT -1,
    conv_type      TEXT NOT NULL DEFAULT 'chat',  -- chat / system / broadcast
    title          TEXT DEFAULT '',
    parent_conv_id INTEGER,                  -- fork : arborescence des convs
    created_by     INTEGER DEFAULT 0,        -- agent_id (négatif = humain)
    status         TEXT NOT NULL DEFAULT 'open',  -- open / closed
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (parent_conv_id) REFERENCES conversation(conv_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_project ON conversation(project_id, created_at);

CREATE TABLE IF NOT EXISTS message (
    msg_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id     INTEGER NOT NULL,
    agent_id    INTEGER NOT NULL,            -- NÉGATIF = humain (humain_as_agent)
    team_id     INTEGER NOT NULL DEFAULT -1,
    project_id  TEXT NOT NULL DEFAULT '',
    msg_type    TEXT NOT NULL DEFAULT 'text',  -- text / file / system / broadcast…
    content     TEXT NOT NULL,
    attachments TEXT DEFAULT '[]',           -- JSON : liste de paths génériques
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (conv_id) REFERENCES conversation(conv_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON message(conv_id, created_at);

CREATE TABLE IF NOT EXISTS direct_message (
    dm_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_from   INTEGER NOT NULL,           -- NÉGATIF = humain
    agent_to     INTEGER NOT NULL,
    project_id   TEXT DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'request', -- request / reply / info / broadcast
    content      TEXT NOT NULL,
    attachment   TEXT DEFAULT '',            -- path générique
    interruption TEXT NOT NULL DEFAULT 'none',   -- none / entrypoint (FSM plus tard)
    received     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dm_to   ON direct_message(agent_to, received, created_at);
CREATE INDEX IF NOT EXISTS idx_dm_from ON direct_message(agent_from, created_at);

-- Copie des direct_message FLUSHÉS (même schéma + flushed_at) — remplie
-- seulement si le flush_tick est configuré archive=True.
CREATE TABLE IF NOT EXISTS archive_direct_message (
    dm_id        INTEGER PRIMARY KEY,
    agent_from   INTEGER NOT NULL,
    agent_to     INTEGER NOT NULL,
    project_id   TEXT DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'request',
    content      TEXT NOT NULL,
    attachment   TEXT DEFAULT '',
    interruption TEXT NOT NULL DEFAULT 'none',
    received     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    flushed_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS human_choice (
    choice_id    TEXT PRIMARY KEY,
    project_id   TEXT DEFAULT '',
    team_id      INTEGER DEFAULT -1,
    agent_id     INTEGER,                    -- qui demande (négatif = humain)
    task_id      INTEGER,
    sub_task_id  INTEGER,
    question     TEXT NOT NULL,
    options_json TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending / answered
    response     TEXT,
    applied      INTEGER NOT NULL DEFAULT 0,        -- décision consommée (watcher P10)
    asked_at     INTEGER DEFAULT (strftime('%s','now')),
    answered_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_choice_status ON human_choice(status);
CREATE INDEX IF NOT EXISTS idx_choice_task  ON human_choice(task_id, sub_task_id);

CREATE TABLE IF NOT EXISTS question (
    id_question    INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT NOT NULL DEFAULT '',
    team_id        INTEGER NOT NULL DEFAULT 0,
    question       TEXT NOT NULL,
    id_creator     INTEGER,                  -- agent qui pose (négatif = humain)
    status_answering TEXT NOT NULL DEFAULT 'awaiting', -- awaiting / answered / cancelled
    options_json   TEXT,
    max_tours      INTEGER NOT NULL DEFAULT 5,
    tour_courant   INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT DEFAULT (datetime('now')),
    answered_at    TEXT
);

CREATE TABLE IF NOT EXISTS reponse (
    id_reponse  INTEGER PRIMARY KEY AUTOINCREMENT,
    id_question INTEGER NOT NULL,
    id_agent    INTEGER NOT NULL,            -- agent qui vote (négatif = humain)
    model_ref   TEXT DEFAULT '',             -- modèle qui a répondu
    contenu     TEXT NOT NULL,
    jugement    TEXT DEFAULT '',             -- vote A/B/C / NEW / note
    similar_to  INTEGER,                     -- ≈ autre reponse (non tranché)
    created_at  TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (id_question) REFERENCES question(id_question) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_reponse_question ON reponse(id_question);