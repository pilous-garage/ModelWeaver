-- modules.db — annuaire introspectable : modules, sous-modules, routes.
-- Chaque module (DOMAINE sqlite, SERVICE, package services/, AgentsCatalogue)
-- est déclaré ici, avec la liste de ses fonctions EXTERNES (module_routes).
-- Le path de route est le path dotted complet (même langage que env.path) :
--     modules.sqlite.services.add_service | services.service_tick | mw.svc...
-- L'auto-découverte (modules.discover) importe chaque module en try/except :
-- les modules cassés restent déclarés avec status='declared' (routes vides).
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS modules (
    module_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL UNIQUE,        -- dotted complet (modules.sqlite.services)
    name        TEXT NOT NULL,               -- dernier segment
    type        TEXT NOT NULL DEFAULT 'module', -- module | domain | service | api | skills
    parent      TEXT DEFAULT '',             -- dotted du parent ('' = racine)
    description TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'declared', -- declared | discovered | loaded
    version     TEXT DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS module_routes (
    route_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    module_path   TEXT NOT NULL REFERENCES modules(path) ON DELETE CASCADE,
    name          TEXT NOT NULL,             -- nom local de la fonction
    path          TEXT NOT NULL UNIQUE,      -- dotted complet (module + name)
    callable_path TEXT DEFAULT '',           -- si la route vit ailleurs
    signature     TEXT DEFAULT '',           -- chaîne de signature lisible
    description   TEXT DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'func', -- func | builtin | api
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_modules_parent ON modules(parent);
CREATE INDEX IF NOT EXISTS idx_routes_module ON module_routes(module_path);