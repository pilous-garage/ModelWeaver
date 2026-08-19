-- hardware_software.db — logiciels RÉELLEMENT installés sur la machine.
--
-- installed_software : liste des softwares référencés (→ catalogue 'tool'),
--   la recette utilisée (→ catalogue 'tool_recipe'), les cmd (JSON détaillé),
--   les privilèges (tag par défaut hérité du catalogue, réécrit par
--   l'utilisateur via privilege_override).
-- Domaine OUVERT (installer, cli, agents). Pas de token.
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS installed_software (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_ref        TEXT NOT NULL,        -- ref catalogue 'tool' (namespace/name)
    recipe_ref      TEXT DEFAULT '',      -- ref recette 'tool_recipe'
    name            TEXT NOT NULL,
    cmd_json        TEXT DEFAULT '{}',    -- JSON détaillé des commandes
    path_installed  TEXT DEFAULT '',      -- chemin d'installation
    status          TEXT DEFAULT 'installed'
                    CHECK(status IN ('installed','missing','broken','updated')),
    -- Privilèges : tag par défaut (hérité du catalogue tool) + override user
    privilege_tag       TEXT DEFAULT 'default',
    privilege_override  TEXT DEFAULT '',  -- si non vide, écrase privilege_tag
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(tool_ref)
);

CREATE INDEX IF NOT EXISTS idx_sw_tool ON installed_software(tool_ref);
CREATE INDEX IF NOT EXISTS idx_sw_status ON installed_software(status);

-- tool_commands : commandes générées depuis le catalogue 'tool' (cmd_json).
--   Une ligne par (tool_ref, command_name) — la commande détaillée (args,
--   flags, env) en JSON. Référence installed_software pour l'outils installé.
CREATE TABLE IF NOT EXISTS tool_commands (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_ref        TEXT NOT NULL,        -- → installed_software.tool_ref
    command_name    TEXT NOT NULL,
    cmd_json        TEXT DEFAULT '{}',    -- détail : args, flags, env, cwd
    privilege_tag   TEXT DEFAULT 'default',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(tool_ref, command_name)
);
CREATE INDEX IF NOT EXISTS idx_tc_tool ON tool_commands(tool_ref);
