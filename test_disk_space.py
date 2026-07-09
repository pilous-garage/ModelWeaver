#!/usr/bin/env python3
"""Test l'installation d'un outil dans Docker et mesure l'espace disque.

Workflow:
1. Démarre un container mw-v0.5.7 depuis ubuntu:24.04
2. Monte le cache local dans le container
3. Mesure du -sb / avant install
4. Installe l'outil depuis le cache
5. Nettoie le cache du container (sauf --keep-cache)
6. Mesure du -sb / après install
7. size_download = taille des fichiers de cache de l'outil
   size_disk = diff avant/après
8. Met à jour le .mw.yaml avec size_download et size_disk

Usage:
    python3 test_disk_space.py <ref> [--keep-cache] [--recipe path/to/x.mw.yaml]
"""
import sys
import os
import subprocess
import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CONTAINER_NAME = "mw-v0.5.7"
BASE_IMAGE = "ubuntu:24.04"


def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def docker_exec(container, cmd, **kwargs):
    return run(["docker", "exec", container, "bash", "-c", cmd], **kwargs)


def main():
    p = argparse.ArgumentParser(description="Test disk space d'une install en Docker")
    p.add_argument("ref", nargs="?", help="Ref de l'outil")
    p.add_argument("--recipe", help="Chemin direct vers .mw.yaml")
    p.add_argument("--keep-cache", action="store_true", help="Garder le cache après install")
    p.add_argument("--force-rebuild", action="store_true", help="Reconstruit le container from scratch")
    args = p.parse_args()

    if not args.ref and not args.recipe:
        p.print_help()
        return 1

    # Repartir du container existant ou créer un nouveau
    existing = run(["docker", "inspect", CONTAINER_NAME])
    if args.force_rebuild or existing.returncode != 0:
        print(f"🔨 Création d'un nouveau container {CONTAINER_NAME} depuis {BASE_IMAGE}...")
        run(["docker", "rm", "-f", CONTAINER_NAME])
        r = run(["docker", "create", "--name", CONTAINER_NAME, BASE_IMAGE, "sleep", "9999"])
        if r.returncode != 0:
            print(f"❌ Impossible de créer le container: {r.stderr}")
            return 1

    print(f"▶️  Démarrage du container {CONTAINER_NAME}...")
    run(["docker", "start", CONTAINER_NAME])

    # Copier le projet dedans (une seule passe)
    print("📂 Copie du projet...")
    tar_proc = subprocess.run(
        ["tar", "cf", "-",
         "--exclude=.opencode",
         "--exclude=.modelweaver",
         "--exclude=__pycache__",
         "--exclude=node_modules",
         "--exclude=gui/installer/src-tauri/target",
         "-C", str(PROJECT_ROOT), "."],
        stdout=subprocess.PIPE,
    )
    subprocess.run(
        ["docker", "cp", "-", f"{CONTAINER_NAME}:/app"],
        input=tar_proc.stdout,
    )

    # Création des dossiers de cache dans le container
    docker_exec(CONTAINER_NAME, "mkdir -p /app/.modelweaver/cache /app/installed_recipe")

    # Copier le cache local dans le container (pour simuler un cache distant)
    cache_dir = PROJECT_ROOT / ".modelweaver" / "cache"
    if cache_dir.exists():
        print("📦 Copie du cache local dans le container...")
        subprocess.run(
            ["docker", "cp", str(cache_dir) + "/.", f"{CONTAINER_NAME}:/app/.modelweaver/cache"],
        )

    # Installation de python3 dans le container (nécessaire pour mw)
    print("🐍 Installation de python3 dans le container...")
    docker_exec(CONTAINER_NAME,
        "apt-get update -qq && apt-get install -y -qq python3 python3-pip > /dev/null 2>&1")

    # Mesure AVANT install
    print("📏 Mesure avant install...")
    before = docker_exec(CONTAINER_NAME, "du -sb / | awk '{print $1}'")
    size_before = int(before.stdout.strip())
    print(f"  Avant: {size_before:,} octets ({size_before/1024/1024:.1f} MB)")

    # Installer l'outil via bin/mw_install.py
    install_cmd = "cd /app && python3 bin/mw_install.py"
    if args.recipe:
        install_cmd += f" --recipe {args.recipe}"
    else:
        install_cmd += f" {args.ref}"

    print(f"📦 Installation: {install_cmd}")
    r = docker_exec(CONTAINER_NAME, install_cmd)
    install_ok = r.returncode == 0
    print(r.stdout)
    if r.stderr:
        print(r.stderr)

    if not install_ok:
        print("⚠️  Install échouée — mais on mesure quand même")

    # Nettoyage du cache dans le container (sauf --keep-cache)
    if not args.keep_cache:
        print("🧹 Nettoyage du cache...")
        docker_exec(CONTAINER_NAME, "rm -rf /app/.modelweaver/cache/* /tmp/*.tar.gz /tmp/*.zip 2>/dev/null || true")
        # Nettoie apt cache aussi
        docker_exec(CONTAINER_NAME, "apt-get clean 2>/dev/null || true")

    # Mesure APRÈS install + cleanup
    print("📏 Mesure après install...")
    after = docker_exec(CONTAINER_NAME, "du -sb / | awk '{print $1}'")
    size_after = int(after.stdout.strip())
    print(f"  Après: {size_after:,} octets ({size_after/1024/1024:.1f} MB)")

    # Calculs
    size_disk = size_after - size_before
    # size_download = taille des fichiers dans le cache pour cet outil (approximatif)
    cache_size = docker_exec(CONTAINER_NAME, "du -sb /app/.modelweaver/cache 2>/dev/null | awk '{print $1}'")
    size_download = int(cache_size.stdout.strip()) if cache_size.returncode == 0 and cache_size.stdout.strip() else 0

    print()
    print("━" * 40)
    print(f"  ref            : {args.ref or args.recipe}")
    print(f"  size_download  : {size_download:,} octets ({size_download/1024/1024:.1f} MB)")
    print(f"  size_disk      : {size_disk:,} octets ({size_disk/1024/1024:.1f} MB)")
    print(f"  delta (%)      : {((size_after - size_before) / max(size_before,1)) * 100:.2f}%")
    print("━" * 40)

    # Mettre à jour le .mw.yaml local avec les métriques
    if args.recipe:
        update_yaml_metric(Path(args.recipe), size_disk, size_download)
    elif args.ref:
        recipe_path = PROJECT_ROOT / "install_recipe" / f"{args.ref}.mw.yaml"
        if recipe_path.exists():
            update_yaml_metric(recipe_path, size_disk, size_download)

    # Garder le container (comme demandé)
    print(f"✅ Container {CONTAINER_NAME} conservé.")
    print(f"   Pour investiguer: docker exec -it {CONTAINER_NAME} bash")
    print(f"   Pour nettoyer: docker rm -f {CONTAINER_NAME}")
    return 0


def update_yaml_metric(yaml_path: Path, size_disk: int, size_download: int):
    """Ajoute size_download et size_disk dans le .mw.yaml (en bas)."""
    content = yaml_path.read_text()
    if "size_download:" in content and "size_disk:" in content:
        # Mettre à jour les valeurs existantes
        import re
        content = re.sub(r"size_download:\s*\d+", f"size_download: {size_download}", content)
        content = re.sub(r"size_disk:\s*\d+", f"size_disk: {size_disk}", content)
    else:
        # Ajouter en haut du fichier (avant la ligne `versions:`)
        lines = content.splitlines()
        new_lines = []
        inserted = False
        for line in lines:
            if not inserted and line.startswith("versions:"):
                new_lines.append(f"# Métriques mesurées en Docker (test_disk_space.py)")
                new_lines.append(f"size_download: {size_download}  # octets téléchargés")
                new_lines.append(f"size_disk: {size_disk}  # octets sur disque après install")
                new_lines.append("")
                inserted = True
            new_lines.append(line)
        content = "\n".join(new_lines) + "\n"
    yaml_path.write_text(content)
    print(f"📝 Mis à jour: {yaml_path}")


if __name__ == "__main__":
    sys.exit(main())