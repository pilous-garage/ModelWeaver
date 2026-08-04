from __future__ import annotations

from typing import Dict


SCHEMA_VERSION: int = 1

DDL_STATEMENTS: Dict[str, str] = {
    "create_database": """
        CREATE TABLE IF NOT EXISTS database_meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """,
    "create_example_table": """
        CREATE TABLE IF NOT EXISTS example (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """,
}

INDEX_STATEMENTS: Dict[str, str] = {
    "idx_example_name": "CREATE INDEX IF NOT EXISTS idx_example_name ON example(name);",
}


def get_schema_definition() -> Dict[str, str]:
    return {
        **DDL_STATEMENTS,
        **INDEX_STATEMENTS,
    }
