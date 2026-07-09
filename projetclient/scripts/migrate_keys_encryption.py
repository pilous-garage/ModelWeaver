#!/usr/bin/env python3
"""Migre les clés API vers un format chiffré."""
import sys
from pathlib import Path
from projetclient.sql.db import ModelWeaverDB
from projetclient.modules.security.vault import vault

def migrate():
    db = ModelWeaverDB()
    keys = db.keys.list_all()
    print(f"Migration de {len(keys)} clés...")
    
    count = 0
    for k in keys:
        ref = k["ref"]
        raw_val = k["key_value"]
        
        # On ne chiffre que si la valeur ne ressemble pas déjà à un token Fernet
        # (Fernet tokens commencent généralement par gAAAA)
        if raw_val and not raw_val.startswith("gAAAA"):
            encrypted = vault.encrypt(raw_val)
            db.conn.execute("UPDATE api_keys SET key_value = ? WHERE ref = ?", (encrypted, ref))
            count += 1
            
    db.commit()
    print(f"✅ {count} clés chiffrées avec succès.")

if __name__ == "__main__":
    migrate()
