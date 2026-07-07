import sys
import os
import subprocess
from pathlib import Path

# Add the current directory to sys.path so we can import modules
sys.path.append("/app")

from modules.installer.installer import Installer
from modules.catalogue.catalogue import Catalogue
from modules.key_manager.key_manager import KeyManager
from modules.key_manager.onboarder import Onboarder

def main():
    print("🚀 Starting Installation inside Docker...")
    
    # 1. Setup paths
    app_dir = Path("/app")
    venv_dir = app_dir / ".venv"
    cache_dir = app_dir / ".modelweaver_cache"
    cache_dir.mkdir(exist_ok=True)
    
    # Create virtual environment if it doesn't exist
    if not venv_dir.exists():
        print(f"📦 Creating virtual environment in {venv_dir}...")
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    
    venv_python = venv_dir / "bin" / "python3"
    venv_pip = venv_dir / "bin" / "pip"

    # In a real scenario, we'd use the project's .env
    env_path = app_dir / ".env"
    
    # 2. Initialize modules
    installer = Installer(cache_dir=cache_dir)
    catalogue = Catalogue(data_dir=app_dir / ".modelweaver/catalogue_data", cache_dir=cache_dir)
    key_manager = KeyManager(vault_path=app_dir / ".modelweaver/vault.json")
    
    # 3. Install dependencies
    print("Step 1: Installing system dependencies...")
    deps = ["curl", "git", "python3-requests"]
    results = installer.install_dependencies(deps)
    print(f"Dependencies results: {results}")

    # Install python dependencies into the venv
    print("Step 1.5: Installing Python packages into venv...")
    python_packages = ["litellm", "fastapi", "uvicorn", "pydantic", "gitingest", "pyyaml", "requests"]
    for pkg in python_packages:
        print(f"Installing {pkg}...")
        try:
            subprocess.run([str(venv_pip), "install", pkg], check=True, capture_output=True, text=True)
            print(f"✅ {pkg} installed.")
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to install {pkg}: {e.stderr}")
            sys.exit(1)

    # Debug: List installed python packages in venv
    print("Listing installed python packages in venv:")
    subprocess.run([str(venv_pip), "list"], check=True)

    # 4. Onboard keys from .env
    if env_path.exists():
        print("Step 2: Onboarding keys from .env...")
        onboarder = Onboarder(key_manager, catalogue=catalogue)
        onboarder.onboard_from_env(env_path)
    else:
        print("⚠️  No .env file found in /app. Skipping onboarding.")

    # 5. Sync catalogue
    print("Step 3: Syncing catalogue...")
    catalogue.sync_with_remote()

    print("\n✅ Installation inside container completed successfully!")

if __name__ == "__main__":
    main()
