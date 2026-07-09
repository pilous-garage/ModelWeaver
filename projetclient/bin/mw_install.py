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
        description="Installe un ou plusieurs outils via catalogue ou recette .mw.yaml",
    )
    p.add_argument("refs", nargs="*", help="Refs des outils dans le catalogue")
    p.add_argument("--recipe", help="Chemin direct vers un fichier .mw.yaml (installe cet outil uniquement)")
    p.add_argument("--version", help="Version spécifique à installer")
    p.add_argument("--keep-cache", action="store_true",
                   help="Garder les fichiers téléchargés après install")
    p.add_argument("--uninstall", action="store_true",
                   help="Désinstaller au lieu d'installer")
    p.add_argument("--all", action="store_true",
                   help="Installer tous les outils du catalogue")
    p.add_argument("--manager", help="Forcer l'utilisation d'un gestionnaire de paquets spécifique")
    args = p.parse_args()

    if not args.refs and not args.recipe and not args.all:
        p.print_help()
        return 1

    # Mise à jour de l'état système via Checker
    from modules.checker.checker import Checker
    db = ModelWeaverDB()
    checker = Checker()
    checker.update_local_db(db)

    recipe_parser = RecipeParser(project_root=PROJECT_ROOT)
    installer = Installer(project_root=PROJECT_ROOT)

    tools_to_install = []

    if args.recipe:
        # Mode --recipe : priorité absolue
        recipe_path = Path(args.recipe)
        if not recipe_path.exists():
            print(f"❌ Fichier recette introuvable: {recipe_path}")
            return 1
        content = recipe_path.read_text()
        recipe = recipe_parser._yaml_simple_parse(content)
        if not recipe:
            print(f"❌ Impossible de parser la recette: {recipe_path}")
            return 1
        name = recipe.get("name", recipe_path.stem)
        
        # Enrichir avec la BDD si possible
        tool_db = db.tools.get(name)
        if tool_db:
            tool_info = {**tool_db, "recipe_path": str(recipe_path)}
        else:
            tool_info = {"ref": name, "recipe_path": str(recipe_path)}
        tools_to_install.append(tool_info)
    elif args.all:
        # Installer tout le catalogue
        all_tools = db.tools.list_all()
        for t in all_tools:
            # Enrichir avec le catalogue distant si possible
            if db.remote_catalogue:
                remote_tool = db.remote_catalogue.get_tool(t["ref"])
                if remote_tool:
                    t = {**t, **remote_tool}
            tools_to_install.append(t)
    else:
        # Installer la liste de refs
        for ref in args.refs:
            tool = db.tools.get(ref)
            if not tool:
                # Essayer le catalogue distant
                if db.remote_catalogue:
                    tool = db.remote_catalogue.get_tool(ref)
            
            if not tool:
                # Essayer de charger la recette directement (GitHub/Local)
                recipe = recipe_parser.load_recipe(ref)
                if recipe:
                    tool = {"ref": ref, "recipe_path": "global.yaml", "name": recipe.get("name", ref)}
                else:
                    print(f"⚠️  Outil '{ref}' introuvable dans le catalogue et aucune recette trouvée, ignoré.")
                    continue
            
            # Enrichir avec le catalogue distant si possible
            if db.remote_catalogue:
                remote_tool = db.remote_catalogue.get_tool(ref)
                if remote_tool:
                    tool = {**tool, **remote_tool}
            
            tools_to_install.append(tool)

    if not tools_to_install:
        print("❌ Aucun outil à installer.")
        return 1

    def progress(percent, message):
        print(f"  [{percent:3d}%] {message}")

    # Boucle d'installation
    success_count = 0
    for tool in tools_to_install:
        name = tool.get("name", tool.get("ref", "unknown"))
        if args.uninstall:
            print(f"🗑️  Désinstallation de {name}...")
            ok = recipe_parser.execute_uninstall(
                recipe_parser.load_recipe(name), args.version, progress)
        else:
            print(f"📦 Installation de {name}...")
            
            # Si on force un manager, on vérifie s'il est compatible avec l'outil
            # sinon on laisse l'installer choisir le meilleur
            current_manager = args.manager
            if current_manager:
                recipe = recipe_parser.load_recipe(name)
                if recipe:
                    # On vérifie si le manager forcé existe pour cet outil
                    if not recipe_parser.resolve(recipe, forced_manager=current_manager):
                        print(f"  ⚠️  Manager {current_manager} incompatible avec {name}, fallback au meilleur manager")
                        current_manager = None

            ok = installer.install(
                tool, 
                progress_callback=progress,
                keep_cache=args.keep_cache,
                forced_manager=current_manager
            )
        
        if ok:
            print(f"✅ {name} {'désinstallé' if args.uninstall else 'installé'}")
            success_count += 1
        else:
            print(f"❌ {name} : échec de l'opération")

    return 0 if success_count == len(tools_to_install) else 1


if __name__ == "__main__":
    sys.exit(main())