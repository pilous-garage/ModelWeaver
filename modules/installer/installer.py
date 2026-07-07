import subprocess
import platform
import os
from pathlib import Path
from typing import List, Dict, Any, Optional

class Installer:
    def __init__(self, cache_dir: Optional[Path] = None):
        self.os_type = platform.system()
        self.distro = self._get_distro()
        self.cache_dir = cache_dir or Path(__file__).parent.parent / ".modelweaver" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_distro(self) -> str:
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("ID="):
                            return line.split("=")[1].strip().strip('"')
        except Exception:
            pass
        return "unknown"

    def install_package(self, package_name: str) -> bool:
        """Installs a package using the system package manager, checking cache first."""
        # In a real V0.2 installer, we would check if the package exists in cache_dir
        # For this test, we simulate the check.
        cache_path = self.cache_dir / f"{package_name}.deb"
        if cache_path.exists():
            print(f"📦 Using cached package: {package_name}")
            return True

        if self.os_type == "Linux" and self.distro == "ubuntu":
            return self._install_apt(package_name)
        elif self.os_type == "Darwin":
            return self._install_brew(package_name)
        else:
            print(f"Unsupported OS/Distro: {self.os_type} {self.distro}")
            return False

    def _install_apt(self, package_name: str) -> bool:
        print(f"Installing {package_name} via apt...")
        
        # Determine if we need sudo. In Docker, we usually don't.
        use_sudo = False
        try:
            if os.getuid() != 0:
                use_sudo = True
        except AttributeError:
            # Handle cases where getuid() is not available
            pass
            
        cmd_prefix = ["sudo"] if use_sudo else []
        
        try:
            subprocess.run(cmd_prefix + ["apt-get", "update"], check=True, capture_output=True)
            subprocess.run(cmd_prefix + ["apt-get", "install", "-y", package_name], check=True, capture_output=True)
            # Simulate caching the package
            (self.cache_dir / f"{package_name}.deb").touch()
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to install {package_name} via apt: {e.stderr.decode()}")
            return False
        except Exception as e:
            print(f"An error occurred during apt installation: {e}")
            return False

    def _install_brew(self, package_name: str) -> bool:
        print(f"Installing {package_name} via brew...")
        try:
            subprocess.run(["brew", "install", package_name], check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Failed to install {package_name} via brew: {e.stderr.decode()}")
            return False

    def install_dependencies(self, dependencies: List[str]) -> Dict[str, bool]:
        """Installs a list of dependencies."""
        results = {}
        for dep in dependencies:
            results[dep] = self.install_package(dep)
        return results

if __name__ == "__main__":
    # Quick test
    installer = Installer(cache_dir=Path("test_cache"))
    print(f"OS: {installer.os_type}, Distro: {installer.distro}, Cache: {installer.cache_dir}")
    # installer.install_package("curl")
