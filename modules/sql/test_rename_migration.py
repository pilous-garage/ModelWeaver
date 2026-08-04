import sqlite3
import tempfile
import os
from pathlib import Path

from modules.sql.migrations import MigrationManager


def test_rename_key_endpoint_models_to_provider_models_mapping():
    tmpdir = tempfile.TemporaryDirectory()
    db_path = Path(tmpdir.name) / "catalogue.db"
    migrations_dir = Path(__file__).parent / "migrations"

    # 1) Créer une DB avec l'ancien schéma + données de test
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS catalogue_providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS catalogue_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ref TEXT UNIQUE NOT NULL
        );

        CREATE TABLE IF NOT EXISTS provider_endpoints (
            endpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS key_endpoint_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_id INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
            endpoint_id INTEGER NOT NULL REFERENCES provider_endpoints(endpoint_id) ON DELETE CASCADE,
            key_ref TEXT NOT NULL,
            model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
            provider_model_name TEXT NOT NULL,
            declared INTEGER DEFAULT 0,
            available INTEGER DEFAULT 0,
            last_checked_at INTEGER,
            last_error TEXT,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            UNIQUE(endpoint_id, key_ref, model_id)
        );

        INSERT INTO catalogue_providers (ref) VALUES ('prov1');
        INSERT INTO catalogue_models (ref) VALUES ('model1');
        INSERT INTO provider_endpoints (provider_id) VALUES (1);
        INSERT INTO key_endpoint_models
            (provider_id, endpoint_id, key_ref, model_id, provider_model_name, declared, available)
        VALUES (1, 1, 'key1', 1, 'provider/model-1', 1, 1);
        """
    )
    conn.commit()
    conn.close()

    # 2) Appliquer la migration
    manager = MigrationManager(db_path)
    applied = manager.apply_migrations(migrations_dir)
    assert applied == 1, f"1 migration attendue, {applied} appliquée(s)"

    # 3) Vérifier que la nouvelle table existe et contient les données
    conn = sqlite3.connect(str(db_path))
    cur = conn.execute("SELECT COUNT(*) FROM provider_models_mapping")
    count = cur.fetchone()[0]
    assert count == 1, f"1 ligne attendue, {count} trouvée(s)"

    cur = conn.execute(
        "SELECT provider_id, endpoint_id, key_ref, model_id, provider_model_name, declared, available "
        "FROM provider_models_mapping"
    )
    row = cur.fetchone()
    assert row == (1, 1, 'key1', 1, 'provider/model-1', 1, 1), f"Données inattendues : {row}"

    # 4) Vérifier que l'ancienne table n'existe plus
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='key_endpoint_models'"
    )
    assert cur.fetchone() is None, "Ancienne table key_endpoint_models toujours présente"

    conn.close()
    tmpdir.cleanup()
    print("OK : migration de renommage validée.")


if __name__ == "__main__":
    test_rename_key_endpoint_models_to_provider_models_mapping()
