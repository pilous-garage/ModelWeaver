-- Migration : renommer key_endpoint_models en provider_models_mapping
-- Préservation des données existantes.

-- 1. Créer la nouvelle table avec la même structure
CREATE TABLE IF NOT EXISTS provider_models_mapping (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id         INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
    endpoint_id         INTEGER NOT NULL REFERENCES provider_endpoints(endpoint_id) ON DELETE CASCADE,
    key_ref             TEXT NOT NULL,
    model_id            INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
    provider_model_name TEXT NOT NULL,
    declared            INTEGER DEFAULT 0,
    available           INTEGER DEFAULT 0,
    last_checked_at     INTEGER,
    last_error          TEXT,
    created_at          INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(endpoint_id, key_ref, model_id)
);

-- 2. Copier les données existantes
INSERT INTO provider_models_mapping
    (id, provider_id, endpoint_id, key_ref, model_id, provider_model_name, declared, available, last_checked_at, last_error, created_at)
SELECT id, provider_id, endpoint_id, key_ref, model_id, provider_model_name, declared, available, last_checked_at, last_error, created_at
FROM key_endpoint_models;

-- 3. Supprimer les anciens index
DROP INDEX IF EXISTS idx_kem_provider;
DROP INDEX IF EXISTS idx_kem_endpoint;
DROP INDEX IF EXISTS idx_kem_key;
DROP INDEX IF EXISTS idx_kem_declared;
DROP INDEX IF EXISTS idx_kem_available;

-- 4. Supprimer l'ancienne table
DROP TABLE IF EXISTS key_endpoint_models;

-- 5. Créer les nouveaux index sur la table renommée
CREATE INDEX IF NOT EXISTS idx_pmm_provider ON provider_models_mapping(provider_id);
CREATE INDEX IF NOT EXISTS idx_pmm_endpoint ON provider_models_mapping(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_pmm_key ON provider_models_mapping(key_ref);
CREATE INDEX IF NOT EXISTS idx_pmm_declared ON provider_models_mapping(declared);
CREATE INDEX IF NOT EXISTS idx_pmm_available ON provider_models_mapping(available);
