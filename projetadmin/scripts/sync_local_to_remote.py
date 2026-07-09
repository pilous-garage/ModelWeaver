import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from sql.db import ModelWeaverDB, TursoCatalogueDB

load_dotenv()

def sync_local_to_remote():
    print("🚀 Syncing local catalogue to Turso...")
    
    try:
        db = ModelWeaverDB()
        remote = db.remote_catalogue
        if not remote:
            print("❌ Remote catalogue not available.")
            return
            
        # Sync Tools
        # Note: We use the legacy 'tools' table from local DB if it exists, 
        # but we should use the new relational structure if possible.
        # For now, let's just use what's in the local catalogue.db
        
        local_cat_path = Path(".modelweaver/catalogue.db")
        conn = sqlite3.connect(local_cat_path)
        conn.row_factory = sqlite3.Row
        
        # We want to push catalogue_tools to Turso
        cur = conn.execute("SELECT * FROM catalogue_tools")
        tools = cur.fetchall()
        
        print(f"Found {len(tools)} tools in local catalogue.db")
        
        count = 0
        for tool in tools:
            success = remote.upsert_tool(dict(tool))
            if success:
                count += 1
        
        print(f"✅ Successfully pushed {count}/{len(tools)} tools to Turso.")
        conn.close()
        
    except Exception as e:
        print(f"❌ Error during sync: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    sync_local_to_remote()
