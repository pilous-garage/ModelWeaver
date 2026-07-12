import json
import shutil
import platform
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
import psutil

class Checker:
    def __init__(self, state_file: Optional[Path] = None):
        if state_file:
            self.state_file = Path(state_file)
        else:
            self.state_file = Path(__file__).parent.parent / ".modelweaver" / "system_state.json"
        
        self.state: Dict[str, Any] = {}

    def check_command(self, command: str) -> bool:
        """Checks if a command exists in the system PATH."""
        return shutil.which(command) is not None

    def get_system_info(self) -> Dict[str, Any]:
        """Gathers basic system information."""
        return {
            "os": platform.system(),
            "os_release": platform.release(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "processor": platform.processor(),
        }

    def get_detected_managers(self) -> List[str]:
        """Detects available package managers on the system."""
        managers = {
            "apt": "apt-get",
            "brew": "brew",
            "winget": "winget",
            "choco": "choco",
            "snap": "snap",
            "pip": "pip",
            "pacman": "pacman",
            "dnf": "dnf",
            "yum": "yum",
            "zypper": "zypper",
            "apk": "apk",
            "emerge": "emerge",
            "nix": "nix",
            "flatpak": "flatpak",
            "cargo": "cargo",
            "npm": "npm",
            "go": "go",
        }
        detected = []
        for name, cmd in managers.items():
            if shutil.which(cmd):
                detected.append(name)
        return detected

    def update_local_db(self, db: Any) -> None:
        """Saves the current system state into the local DB."""
        info = self.get_system_info()
        managers = self.get_detected_managers()
        state = {
            "os": info["os"],
            "architecture": info["architecture"],
            "os_version": info["os_version"],
            "detected_managers": managers,
        }
        db.system_state.save(state)


    def get_hardware_info(self) -> Dict[str, Any]:
        """Gathers hardware information."""
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        
        return {
            "ram_total_gb": round(mem.total / (1024**3), 2),
            "ram_available_gb": round(mem.available / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "disk_free_gb": round(disk.free / (1024**3), 2),
        }

    def check_dependencies(self) -> List[Dict[str, Any]]:
        """Checks for required dependencies."""
        dependencies = ["python3", "git", "curl"]
        results = []
        for dep in dependencies:
            results.append({
                "name": dep,
                "present": self.check_command(dep),
                "path": shutil.which(dep) if self.check_command(dep) else None
            })
        return results

    def run_all_checks(self) -> Dict[str, Any]:
        """Runs all available checks and returns the result."""
        self.state = {
            "system": self.get_system_info(),
            "hardware": self.get_hardware_info(),
            "dependencies": self.check_dependencies(),
        }
        return self.state

    def save_state(self) -> None:
        """Saves the system state to a JSON file."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                import json
                json.dump(self.state, f, indent=2)
        except IOError as e:
            print(f"Error saving system state: {e}")

if __name__ == "__main__":
    # Quick test
    from pathlib import Path
    checker = Checker(state_file=Path("test_state.json"))
    state = checker.run_all_checks()
    print(json.dumps(state, indent=2))
    checker.save_state()
    import os
    if Path("test_state.json").exists(): os.remove("test_state.json")
