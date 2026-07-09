import os
import subprocess
import tarfile
import shutil
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(".env"))

CLIENT_DIR = Path("projetclient")
ARCHIVE_NAME = "modelweaver_client.tar.gz"
MAX_SIZE_MB = 50

def get_dir_size(path: Path) -> float:
    """Retourne la taille du dossier en Mo."""
    total_size = 0
    for entry in path.rglob('*'):
        if entry.is_file():
            total_size += entry.stat().st_size
    return total_size / (1024 * 1024)

def create_archive():
    print(f"📦 Création de l'archive {ARCHIVE_NAME}...")
    with tarfile.open(ARCHIVE_NAME, "w:gz") as tar:
        # On ajoute le contenu du dossier projetclient, pas le dossier lui-même
        tar.add(CLIENT_DIR, arcname=".")
    print(f"✅ Archive créée : {ARCHIVE_NAME}")

def upload_to_github_gh_cli():
    """Utilise la GitHub CLI (gh) pour gérer la release."""
    if not shutil.which("gh"):
        print("❌ GitHub CLI ('gh') n'est pas installé.")
        print("Installez-le via: brew install gh / sudo apt install gh")
        return False

    tag = "v0.1-latest"
    
    # On tente de créer la release. Si elle existe déjà, on utilise upload.
    # --clobber permet d'écraser les assets existants sur certaines versions de gh
    try:
        print(f"🚀 Tentative de création/mise à jour de la release {tag}...")
        # On tente de créer la release avec l'archive
        # Si elle existe, on upload simplement l'archive
        result = subprocess.run(
            ["gh", "release", "create", tag, ARCHIVE_NAME, "--title", "Latest Client Build", "--notes", "Automatic upload of the latest ModelWeaver client archive."],
            capture_output=True, text=True
        )
        
        if result.returncode != 0:
            # Si la release existe déjà, on upload l'asset
            print("ℹ️  Release existante, mise à jour de l'asset...")
            result = subprocess.run(
                ["gh", "release", "upload", tag, ARCHIVE_NAME, "--clobber"],
                capture_output=True, text=True
            )
            
        if result.returncode == 0:
            print(f"✅ Archive uploadée avec succès via gh CLI dans la release {tag} !")
            return True
        else:
            print(f"❌ Erreur gh CLI: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ Erreur inattendue: {e}")
        return False

def main():
    if not CLIENT_DIR.exists():
        print("❌ Dossier projetclient introuvable.")
        return

    size = get_dir_size(CLIENT_DIR)
    print(f"📏 Taille du dossier client : {size:.2f} Mo")

    if size > MAX_SIZE_MB:
        print(f"⚠️  ATTENTION : Le dossier client ({size:.2f} Mo) dépasse la limite de {MAX_SIZE_MB} Mo !")
        choice = input("Continuer quand même ? (y/n): ")
        if choice.lower() != 'y':
            print("❌ Opération annulée.")
            return

    create_archive()
    if upload_to_github_gh_cli():
        print("✨ Processus terminé avec succès.")
    else:
        print("❌ L'upload a échoué.")

if __name__ == "__main__":
    main()
