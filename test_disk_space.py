#!/usr/bin/env python3
"""Test l'installation d'un outil dans Docker et mesure l'espace disque.
Enregistre les résultats dans la base de données distante Turso.
"""
import sys
import os
import subprocess
import argparse
from pathlib import Path
from dotenv import load_dotenv

# Charger les variables d'environnement pour Turso
load_dotenv()

# Ajuster le PYTHONPATH pour trouver les modules dans projetclient
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root / "projetclient"))

try:
    from sql.db import ModelWeaverDB
except ImportError as e:
    print(f"❌ Erreur d'import: {e}")
    sys.exit(1)

# Configuration
CONTAINER_NAME = "mw-v0.5.7"
BASE_IMAGE = "ubuntu:24.04"

def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)

def docker_exec(container, cmd, **kwargs):
    return run(["docker", "exec", container, "bash", "-c", cmd], **kwargs)

def main():
    p = argparse.ArgumentParser(description="Mesure l'espace disque d'une install en Docker")
    p.add_argument("ref", help="Ref de l'outil")
    p.add_argument("--recipe", help="Chemin direct vers .mw.yaml")
    p.add_argument("--keep-cache", action="store_true", help="Garder le cache après install")
    p.add_argument("--force-rebuild", action="store_true", help="Reconstruit le container")
    args = p.parse_args()

    # Utiliser une DB locale temporaire pour éviter les problèmes de permission/chemin
    db = ModelWeaverDB(db_path=project_root / "temp_metrics.db")

    # 1. Gestion du container
    existing = run(["docker", "inspect", CONTAINER_NAME])
    if args.force_rebuild or existing.returncode != 0:
        print(f"🔨 Création du container {CONTAINER_NAME}...")
        run(["docker", "rm", "-f", CONTAINER_NAME])
        run(["docker", "create", "--name", CONTAINER_NAME, BASE_IMAGE, "sleep", "9999"])

    run(["docker", "start", CONTAINER_NAME])
    docker_exec(CONTAINER_NAME, "mkdir -p /app")

    # 2. Copie du projet (On copie projetclient dans /app)
    print("📂 Copie du projet...")
    subprocess.run(
        f"tar -cf - -C {project_root}/projetclient . | docker exec -i {CONTAINER_NAME} tar -xf - -C /app",
        shell=True
    )

    docker_exec(CONTAINER_NAME, "mkdir -p /app/.modelweaver/cache /app/installed_recipe")
    
    # Copie cache local
    cache_dir = project_root / ".modelweaver" / "cache"
    if cache_dir.exists():
        subprocess.run(["docker", "cp", str(cache_dir) + "/.", f"{CONTAINER_NAME}:/app/.modelweaver/cache"])

    # Python et dépendances
    print("🐍 Préparation Python...")
    docker_exec(CONTAINER_NAME, "apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-yaml > /dev/null 2>&1")

    # 3. Mesure AVANT
    before = docker_exec(CONTAINER_NAME, "du -sb / | awk '{print $1}'")
    size_before = int(before.stdout.strip())
    print(f"📏 Avant: {size_before/1024/1024:.1f} MB")

    # 4. Installation
    install_cmd = "cd /app && python3 bin/mw_install.py"
    if args.recipe:
        install_cmd += f" --recipe {args.recipe}"
    else:
        install_cmd += f" {args.ref}"

    print(f"📦 Installation: {args.ref}...")
    r = docker_exec(CONTAINER_NAME, install_cmd)
    print(r.stdout)
    
    # 5. Nettoyage et Mesure APRÈS
    if not args.keep_cache:
        docker_exec(CONTAINER_NAME, "rm -rf /app/.modelweaver/cache/* /tmp/*.tar.gz /tmp/*.zip 2>/dev/null || true")
        docker_exec(CONTAINER_NAME, "apt-get clean 2>/dev/null || true")

    after = docker_exec(CONTAINER_NAME, "du -sb / | awk '{print $1}'")
    size_after = int(after.stdout.strip())
    print(f"📏 Après: {size_after/1024/1024:.1f} MB")

    size_disk = size_after - size_before
    
    # Estimation du download (on pourrait faire un run séparé, ici on met 0 ou on estime)
    size_download = 0 

    print(f"\nRésultat pour {args.ref}: Disk Space = {size_disk / 1024 / 1024:.2f} MB")

    # 6. Mise à jour des bases de données
    os_name = "linux"
    arch_name = "x86_64"
    version = "latest"
    manager = "apt" # Default for ubuntu container

    # Local DB (temp)
    with db.transaction():
        db.conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_id TEXT NOT NULL,
                version TEXT NOT NULL,
                os TEXT NOT NULL,
                arch TEXT NOT NULL,
                manager TEXT NOT NULL,
                size_download INTEGER DEFAULT 0,
                size_disk INTEGER DEFAULT 0,
                last_measured DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tool_id, version, os, arch, manager)
            )
        """)
        db.conn.execute("""
            INSERT INTO tool_metrics (tool_id, version, os, arch, manager, size_download, size_disk)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tool_id, version, os, arch, manager) 
            DO UPDATE SET size_download=excluded.size_download, size_disk=excluded.size_disk
        """, (args.ref, version, os_name, arch_name, manager, size_download, size_disk))

    # Remote DB update
    if db.remote_catalogue:
        res = db.remote_catalogue.client.execute("SELECT id FROM catalogue_tools WHERE ref = ?", (args.ref,))
        if res:
            tool_id = res[0][0]
            db.remote_catalogue.update_metrics(tool_id, version, os_name, arch_name, manager, size_download, size_disk)
            print("✅ Remote DB updated.")

    print("✅ Metrics saved.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
