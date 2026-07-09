import os
import subprocess
import tarfile
import shutil
from pathlib import Path
from dotenv import load_dotenv

RECIPES_DIR = Path("projetadmin/install_recipe")
DATA_BRANCH = "yaml-data"

def upload_file_via_gh(rel_path, content):
    """Pousse un fichier vers GitHub en utilisant la GitHub CLI."""
    # 1. Récupérer le SHA du fichier existant sur la branche yaml-data
    get_url = f"/repos/pilous-garage/ModelWeaver/contents/{rel_path}?ref={DATA_BRANCH}"
    res = subprocess.run(["gh", "api", get_url], capture_output=True, text=True)
    
    sha = None
    if res.returncode == 0:
        import json
        try:
            data = json.loads(res.stdout)
            sha = data.get("sha")
        except json.JSONDecodeError:
            pass

    # 2. Préparer le payload
    import base64
    import json
    payload = {
        "message": f"Update recipe {rel_path}",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": DATA_BRANCH,
        "sha": sha
    }
    
    # 3. Envoyer via gh api en utilisant un fichier temporaire pour le body
    with open(".gh_payload.json", "w") as f:
        json.dump(payload, f)
        
    put_url = f"/repos/pilous-garage/ModelWeaver/contents/{rel_path}"
    res = subprocess.run(
        ["gh", "api", "-X", "PUT", put_url, "--input", ".gh_payload.json"],
        capture_output=True, text=True
    )
    
    return res.returncode == 0

def main():
    # Check if gh is installed
    if subprocess.run(["which", "gh"], capture_output=True).returncode != 0:
        print("❌ GitHub CLI ('gh') n'est pas installé.")
        return

    print(f"🚀 Starting recipe upload to branch: {DATA_BRANCH} via gh CLI...")
    
    count = 0
    for file_path in RECIPES_DIR.rglob('*'):
        if file_path.suffix in ['.yaml', '.json']:
            rel_path = file_path.relative_to(RECIPES_DIR)
            content = file_path.read_text(encoding='utf-8')
            
            if upload_file_via_gh(rel_path, content):
                print(f"✅ Uploaded: {rel_path}")
                count += 1
            else:
                print(f"❌ Failed: {rel_path}")

    if os.path.exists(".gh_payload.json"):
        os.remove(".gh_payload.json")

    print(f"\n✨ Finished. Uploaded {count} files.")

if __name__ == "__main__":
    main()
