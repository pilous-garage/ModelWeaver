#!/usr/bin/env python3
"""modelweaver-install — Installe un outil via recette .mw.yaml.

Usage:
    modelweaver-install <ref>                    # installe par ref (cherche dans la BDD)
    modelweaver-install --recipe path/to/x.mw.yaml  # installe via un fichier recette direct
    modelweaver-install --recipe path/to/x.mw.yaml --version 8.4.0  # version spécifique
    modelweaver-install <ref> --keep-cache       # garde les fichiers téléchargés
    modelweaver-install <ref> --uninstall        # désinstalle
"""
import sys
import os
import argparse
from pathlib import Path

# Résoudre le projet root depuis le realpath du script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules.installer.installer import Installer
from modules.installer.recipe_parser import RecipeParser
from sql.db import ModelWeaverDB


def main():
    p = argparse.ArgumentParser(
        prog="modelweaver-install",
        description="Installe un outil via recette .mw.yaml",
    )
    p.add_argument("ref", nargs="?", help="Ref de l'outil dans le catalogue")
    p.add_argument("--recipe", help="Chemin direct vers un fichier .mw.yaml")
    p.add_argument("--version", help="Version spécifique à installer")
    p.add_argument("--keep-cache", action="store_true",
                   help="Garder les fichiers téléchargés après install")
    p.add_argument("--uninstall", action="store_true",
                   help="Désinstaller au lieu d'installer")
    args = p.parse_args()

    if not args.ref and not args.recipe:
        p.print_help()
        return 1

    recipe_parser = RecipeParser(project_root=PROJECT_ROOT)
    installer = Installer(project_root=PROJECT_ROOT)

    if args.recipe:
        # Mode --recipe : charge directement le fichier .mw.yaml
        recipe_path = Path(args.recipe)
        if not recipe_path.exists():
            print(f"❌ Fichier recette introuvable: {recipe_path}")
            return 1
        with open(recipe_path) as f:
            pass  # On parse en dessous
        # Parse YAML simple (sans PyYAML)
        content = recipe_path.read_text()
        recipe = recipe_parser._yaml_simple_parse(content)
        if not recipe:
            print(f"❌ Impossible de parser la recette: {recipe_path}")
            return 1
        name = recipe.get("name", recipe_path.stem)
    else:
        # Mode ref : cherche dans la BDD
        db = ModelWeaverDB()
        tool = db.tools.get(args.ref)
        db.close()
        if not tool:
            print(f"❌ Outil '{args.ref}' introuvable dans le catalogue")
            return 1
        recipe = recipe_parser.load_recipe(args.ref)
        if not recipe:
            print(f"❌ Recette introuvable pour '{args.ref}' (recipe_path={tool.get('recipe_path')})")
            return 1
        name = recipe.get("name", args.ref)

    def progress(percent, message):
        print(f"  [{percent:3d}%] {message}")

    if args.uninstall:
        print(f"🗑️  Désinstallation de {name}...")
        ok = recipe_parser.execute_uninstall(recipe, args.version, progress)
        if ok:
            print(f"✅ {name} désinstallé")
            return 0
        print(f"❌ {name} : échec de la désinstallation")
        return 1

    print(f"📦 Installation de {name}...")
    ok = installer.install(
        {"ref": args.ref or name, "name": name,
         "current_version": args.version,
         "recipe_path": args.recipe},
        progress_callback=progress,
    )
    if ok:
        print(f"✅ {name} installé")
        return 0

    print(f"❌ {name} : échec de l'installation")
    return 1


if __name__ == "__main__":
    sys.exit(main())