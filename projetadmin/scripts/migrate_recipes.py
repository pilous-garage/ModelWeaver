import os
import shutil
import yaml
from pathlib import Path
from modules.installer.recipe_parser import RecipeParser

def get_shards(name):
    # Simple sharding: first two characters of the name
    # If name is too short, pad with 'x'
    padded = name.lower().ljust(2, 'x')
    return padded[0], padded[1]

def migrate():
    project_root = Path(__file__).resolve().parent.parent
    old_dir = project_root / "install_recipe"
    new_dir = project_root / "install_recipe_atomic" # Migrate to new dir first to avoid conflicts
    
    # Load the existing parser
    parser = RecipeParser(project_root=project_root)
    
    # Get all recipes
    recipes_files = list(old_dir.glob("*.mw.yaml"))
    print(f"Found {len(recipes_files)} recipes to migrate...")

    for file_path in recipes_files:
        ref = file_path.name.replace(".mw.yaml", "")
        print(f"Migrating {ref}...")
        
        with open(file_path, 'r') as f:
            try:
                # Use PyYAML for migration to be safe
                recipe = yaml.safe_load(f)
            except Exception as e:
                print(f"  ❌ Failed to parse {file_path}: {e}")
                continue

        if not recipe:
            continue

        # Sharding
        s1, s2 = get_shards(ref)
        tool_dir = new_dir / s1 / s2 / ref
        
        # 1. Save global.yaml
        global_data = {
            "name": recipe.get("name", ref),
            "description": recipe.get("description", ""),
            "class": recipe.get("class", "other"),
            "timeout": recipe.get("timeout", 120),
            "pre_install": recipe.get("pre_install", []),
            "post_install": recipe.get("post_install", []),
        }
        
        tool_dir.mkdir(parents=True, exist_ok=True)
        with open(tool_dir / "global.yaml", 'w') as f:
            yaml.dump(global_data, f)

        # 2. Split versions and managers
        versions = recipe.get("versions", {})
        if not versions:
            continue

        default_ver = versions.get("default")
        # We migrate the 'default' version for now, or all if needed.
        # To be extreme, we migrate all versions.
        
        version_keys = [v for v in versions if v != "default"]
        if not version_keys:
            version_keys = [default_ver] if default_ver else ["latest"]
        
        for ver in version_keys:
            ver_block = versions.get(ver, {})
            if not ver_block: continue
            
            # OS loop
            for os_name, managers in ver_block.items():
                if not isinstance(managers, dict): continue
                
                # Manager loop
                for mgr_name, mgr_block in managers.items():
                    # Arch handling
                    # In the current schema, arch is often implicit or in a specific block
                    # We'll use 'all' as default arch if not specified
                    arch = "all"
                    
                    # Target path: .../{os}/{arch}/{manager}.yaml
                    target_dir = tool_dir / os_name / arch
                    target_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Save manager block
                    manager_data = {
                        "version": ver,
                        "block": mgr_block
                    }
                    with open(target_dir / f"{mgr_name}.yaml", 'w') as f:
                        yaml.dump(manager_data, f)

    print("\nMigration complete. Recipes are now in /install_recipe_atomic")
    print("You can now rename /install_recipe to /install_recipe_old and /install_recipe_atomic to /install_recipe")

if __name__ == "__main__":
    migrate()
