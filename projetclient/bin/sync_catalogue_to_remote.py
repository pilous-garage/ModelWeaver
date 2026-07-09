#!/usr/bin/env python3
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from sql.db import ModelWeaverDB

def main():
    load_dotenv()
    print("🚀 Synchronisation du catalogue local -> Turso...")
    
    try:
        db = ModelWeaverDB()
    except Exception as e:
        print(f"❌ Erreur DB: {e}")
        sys.exit(1)

    if not db.remote_catalogue:
        print("❌ Catalogue distant non disponible (TURSO_URL/TOKEN manquants)")
        sys.exit(1)

    # On récupère tous les outils de la table 'tools' locale
    tools = db.tools.list_all() # Assumant que ToolRepository a list_all()
    
    count = 0
    for tool in tools:
        # On prépare les données pour Turso (on ignore recipe_path car non stocké là-bas)
        success = db.remote_catalogue.upsert_tool(tool)
        if success:
            print(f"✅ Synchro: {tool['ref']}")
            count += 1
        else:
            print(f"❌ Échec: {tool['ref']}")

    print(f"\n✨ Terminé : {count}/{len(tools)} outils synchronisés.")
    db.close()

if __name__ == "__main__":
    main()
