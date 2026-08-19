-- keys_schema.sql — domaine de clés API sécurisé.
-- La DB ne contient QUE les métadonnées + un `ref` UUID ; les secrets
-- vivent dans le keyring OS (ou fichier Fernet chiffré). Aucune clé en clair.

CREATE TABLE IF NOT EXISTS api_keys (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ref           TEXT UNIQUE NOT NULL,
    provider_ref  TEXT NOT NULL,
    identity      TEXT DEFAULT 'default',
    tag           TEXT NOT NULL DEFAULT 'paid',
    grade         TEXT,
    health_status TEXT DEFAULT 'unknown',
    locked        INTEGER DEFAULT 0,
    metadata_json TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ak_provider ON api_keys(provider_ref);
