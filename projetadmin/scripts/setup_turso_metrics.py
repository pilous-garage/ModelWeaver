import os
import libsql
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

URL = os.getenv("TURSO_URL")
TOKEN = os.getenv("TURSO_TOKEN")

SCHEMA = Path(__file__).resolve().parent.parent.parent / "modules" / "sql" / "catalogue_schema.sql"


def setup():
    if not URL or not TOKEN:
        print("❌ Missing TURSO_URL or TURSO_TOKEN in .env")
        return

    try:
        client = libsql.connect(URL, auth_token=TOKEN)

        # Exécuter le schéma complet (CREATE TABLE uniquement)
        schema_text = SCHEMA.read_text()
        for stmt in schema_text.split(";"):
            stmt = stmt.strip()
            if not stmt or stmt.upper().startswith("PRAGMA"):
                continue
            client.execute(stmt + ";")
        print("✅ Schéma catalogue créé sur Turso")

        # Clean up legacy table si elle existe
        try:
            client.execute("DROP TABLE IF EXISTS catalogue_tools")
            print("✅ Table legacy catalogue_tools supprimée")
        except Exception:
            pass

        # Table de métriques (optionnelle)
        client.execute("""
            CREATE TABLE IF NOT EXISTS tool_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_id TEXT NOT NULL,
                version TEXT NOT NULL,
                os TEXT NOT NULL,
                arch TEXT NOT NULL,
                manager TEXT NOT NULL,
                size_download INTEGER,
                size_disk INTEGER,
                last_measured DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("✅ Table tool_metrics créée")

    except Exception as e:
        print(f"❌ Setup failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    setup()
