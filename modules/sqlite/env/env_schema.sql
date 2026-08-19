-- env.db — environnement : variables d'env, paths canoniques et alias.
-- Domaines `env` : le socle du "path language" commun (dotted paths).
--
-- Ce que contient le domaine :
--   env_var    : variables d'environnement persistées (scope global/user/
--                project/service), avec priorité de lecture au runtime
--                (os.environ > env_var).
--   path       : ANNUAIRE des chemins canoniques (dotted PRÈS-À-PRÈS des
--                racines réelles : modules/, services/, skills/, data/,
--                db/, api/...). Le resolver n'impose PAS d'être déclaré ici
--                pour fonctionner (l'import python est la source de vérité),
--                mais l'annuaire rend les chemins découvrables/interrogeables.
--   alias      : raccourcis → cible (path dotted, module python ou chemin
--                fs). TOUT le monde passe par le SAME langage de path
--                (modules.sqlite.services.add_service === services.tick.list
--                === alias "mw.svc"), résolu par env.path.resolve().
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS env_var (
    name        TEXT PRIMARY KEY,
    value       TEXT,
    scope       TEXT NOT NULL DEFAULT 'user',   -- global | user | project | service
    description TEXT DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS path (
    path_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type  TEXT NOT NULL DEFAULT 'module', -- module | service | data | skills | api | db
    path        TEXT NOT NULL UNIQUE,           -- chemin canonical dotted (racine réelle)
    parent      TEXT DEFAULT '',                -- path du parent ('' = racine)
    ref         TEXT DEFAULT '',                -- pointeur physique (module python / fs)
    description TEXT DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alias (
    alias       TEXT PRIMARY KEY,
    target      TEXT NOT NULL,                  -- dotted path | module python | chemin fs
    kind        TEXT NOT NULL DEFAULT 'path',   -- path | module | func | fs
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_path_parent ON path(parent);