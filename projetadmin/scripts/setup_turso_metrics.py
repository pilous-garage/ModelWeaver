import os
import libsql
from dotenv import load_dotenv

load_dotenv()

URL = os.getenv("TURSO_URL")
TOKEN = os.getenv("TURSO_TOKEN")

def setup():
    if not URL or not TOKEN:
        print("❌ Missing TURSO_URL or TURSO_TOKEN in .env")
        return

    try:
        client = libsql.connect(URL, auth_token=TOKEN)
        
        print("Creating tool_metrics table...")
        schema = """
        CREATE TABLE IF NOT EXISTS tool_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tool_id TEXT NOT NULL,
            version TEXT NOT NULL,
            os TEXT NOT NULL,
            arch TEXT NOT NULL,
            manager TEXT NOT NULL,
            size_download INTEGER,
            size_disk INTEGER,
            last_measured DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(tool_id) REFERENCES catalogue_tools(id)
        );
        """
        client.execute(schema)
        print("✅ tool_metrics table created successfully!")
        
    except Exception as e:
        print(f"❌ Setup failed: {e}")

if __name__ == "__main__":
    setup()
