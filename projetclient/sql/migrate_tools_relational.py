import sqlite3
from pathlib import Path

db_path = Path(".modelweaver/modelweaver.db")
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()

print("🚀 Migration vers structure relationnelle des outils...")

try:
    # 1. Création des tables
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tool_definitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ref TEXT UNIQUE NOT NULL,
        name TEXT,
        description TEXT,
        tool_class TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tool_variants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tool_id INTEGER NOT NULL,
        os TEXT DEFAULT 'all',
        architecture TEXT DEFAULT 'all',
        version TEXT,
        manager TEXT,
        size_download INTEGER DEFAULT 0,
        size_disk INTEGER DEFAULT 0,
        trust_score REAL DEFAULT 1.0,
        install_count INTEGER DEFAULT 0,
        uninstall_count INTEGER DEFAULT 0,
        is_official BOOLEAN DEFAULT 0,
        last_verified DATETIME,
        FOREIGN KEY(tool_id) REFERENCES tool_definitions(id) ON DELETE CASCADE
    )
    """)

    # 2. Migration des données de l'ancienne table 'tools'
    # On récupère tout ce qu'il y a dans 'tools'
    cur.execute("SELECT ref, name, description, tool_type, current_version, default_download_url FROM tools")
    old_tools = cur.fetchall()

    for tool in old_tools:
        ref, name, desc, tool_type, ver, url = tool
        
        # Insérer dans definitions
        cur.execute("INSERT OR IGNORE INTO tool_definitions (ref, name, description) VALUES (?, ?, ?)", 
                    (ref, name, desc))
        
        # Récupérer l'ID
        cur.execute("SELECT id FROM tool_definitions WHERE ref = ?", (ref,))
        tool_id = cur.fetchone()[0]
        
        # Créer une variante par défaut basée sur les infos existantes
        cur.execute("""
            INSERT INTO tool_variants (tool_id, version, manager, os, architecture)
            VALUES (?, ?, ?, 'all', 'all')
        """, (tool_id, ver, tool_type))

    # 3. Nettoyage
    cur.execute("DROP TABLE tools")
    
    conn.commit()
    print("✅ Migration terminée avec succès.")

except Exception as e:
    conn.rollback()
    print(f"❌ Erreur lors de la migration: {e}")
finally:
    conn.close()
