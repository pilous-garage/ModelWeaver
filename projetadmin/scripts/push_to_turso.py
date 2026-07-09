import os
import sqlite3
import libsql
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

URL = os.getenv("TURSO_URL")
TOKEN = os.getenv("TURSO_TOKEN")
LOCAL_DB = Path(".modelweaver/catalogue.db").resolve()

def migrate():
    if not URL or not TOKEN:
        print("❌ Missing TURSO_URL or TURSO_TOKEN in .env")
        return

    print(f"Connecting to Turso at {URL}...", flush=True)
    try:
        url_http = URL.replace("libsql://", "https://")
        import libsql_client as libsql
        client = libsql.create_client_sync(url_http, auth_token=TOKEN)
        print("✅ Connected successfully to Turso!", flush=True)
    except Exception as e:
        print(f"❌ Connection to Turso failed: {e}")
        return

    
    local_conn = sqlite3.connect(LOCAL_DB)
    cursor = local_conn.cursor()

    print("Fetching local schema...", flush=True)
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = cursor.fetchall()

    for table_sql in tables:
        sql = table_sql[0]
        print(f"Creating table in Turso: {sql[:50]}...", flush=True)
        try:
            client.execute(sql)
        except Exception as e:
            print(f"  ⚠️  Warning during table creation: {e}", flush=True)

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    table_names = [row[0] for row in cursor.fetchall()]

    for table in table_names:
        print(f"Migrating data for table {table}...", flush=True)
        cursor.execute(f"SELECT * FROM {table}")
        rows = cursor.fetchall()
        
        if not rows:
            print(f"  Empty table {table}, skipping...", flush=True)
            continue
            
        col_names = [description[0] for description in cursor.description]
        num_cols = len(col_names)
        placeholders = ", ".join(["?" for _ in range(num_cols)])
        cols_str = ", ".join([f'"{c}"' for c in col_names])
        
        # Batch size
        batch_size = 50
        total_rows = len(rows)
        
        for i in range(0, total_rows, batch_size):
            batch = rows[i : i + batch_size]
            # Construct a multi-row INSERT: INSERT INTO t (c1,c2) VALUES (?,?), (?,?), ...
            batch_placeholders = ", ".join([placeholders] * len(batch))
            sql = f"INSERT INTO {table} ({cols_str}) VALUES {batch_placeholders}"
            
            # Flatten the batch of tuples into a single list of values
            flattened_values = [val for row in batch for val in row]
            
            try:
                client.execute(sql, flattened_values)
            except Exception as e:
                if "UNIQUE constraint failed" not in str(e):
                    print(f"  ❌ Error inserting batch at {i}: {e}", flush=True)

        print(f"  ✅ Migrated {total_rows} rows into {table}", flush=True)

    local_conn.close()
    print("\nMigration to Turso complete!")

if __name__ == "__main__":
    migrate()
