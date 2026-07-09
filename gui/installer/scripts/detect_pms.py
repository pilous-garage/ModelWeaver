import sys
import json
import shutil
import subprocess
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from sql.db import ModelWeaverDB


def detect_package_managers() -> dict:
    detectors = {
        "apt": ["apt-get", "--version"],
        "snap": ["snap", "--version"],
        "brew": ["brew", "--version"],
        "pacman": ["pacman", "--version"],
        "yay": ["yay", "--version"],
        "dnf": ["dnf", "--version"],
        "yum": ["yum", "--version"],
        "zypper": ["zypper", "--version"],
        "apk": ["apk", "--version"],
        "emerge": ["emerge", "--version"],
        "nix": ["nix-env", "--version"],
        "flatpak": ["flatpak", "--version"],
        "pip": ["pip", "--version"],
        "cargo": ["cargo", "--version"],
        "npm": ["npm", "--version"],
        "go": ["go", "version"],
        "winget": ["winget", "--version"],
        "choco": ["choco", "--version"],
    }

    results = {}
    for ref, cmd in detectors.items():
        path = shutil.which(cmd[0])
        version = None
        if path:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                first_line = r.stdout.strip().split("\n")[0] if r.stdout else ""
                version = first_line[:80] if first_line else "detected"
            except Exception:
                version = "detected"
        results[ref] = {
            "detected": path is not None,
            "version": version,
            "path": path,
        }
    return results


def main():
    db = ModelWeaverDB()
    detected = detect_package_managers()

    for ref, info in detected.items():
        db.conn.execute("""
            UPDATE package_managers
            SET detected=?, version=?, updated_at=strftime('%s','now')
            WHERE ref=?
        """, (1 if info["detected"] else 0, info["version"], ref))

    db.commit()

    rows = db.conn.execute(
        "SELECT ref, name, detected, version, install_cmd, os_family FROM package_managers ORDER BY name"
    ).fetchall()

    db.close()

    result = []
    for r in rows:
        result.append({
            "ref": r["ref"],
            "name": r["name"],
            "detected": bool(r["detected"]),
            "version": r["version"],
            "install_cmd": r["install_cmd"],
            "os_family": r["os_family"],
        })

    print(json.dumps({"status": "success", "data": result}), flush=True)


if __name__ == "__main__":
    main()