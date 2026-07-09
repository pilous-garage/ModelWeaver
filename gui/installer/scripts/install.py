import sys
import json
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from modules.installer.installer import Installer
from sql.db import ModelWeaverDB


def progress_callback(percent: int, message: str):
    line = json.dumps({"type": "progress", "percent": percent, "message": message})
    print(line, flush=True)


def main():
    if len(sys.argv) < 2:
        result = {"type": "result", "status": "error", "error": "Tool ref missing"}
        print(json.dumps(result), flush=True)
        return

    tool_ref = sys.argv[1]
    try:
        progress_callback(0, f"Démarrage installation de {tool_ref}...")
        db = ModelWeaverDB()
        tool = db.tools.get(tool_ref)
        if not tool:
            progress_callback(100, f"Outil {tool_ref} introuvable dans le catalogue")
            print(json.dumps({"type": "result", "status": "error",
                              "error": f"Tool {tool_ref} not found in catalog"}), flush=True)
            return

        progress_callback(5, f"Outil trouvé : {tool.get('name', tool_ref)}")

        installer = Installer()
        success = installer.install(tool, progress_callback)
        if success:
            progress_callback(85, "Mise à jour de l'état local...")
        else:
            progress_callback(100, "Échec de l'installation")
            print(json.dumps({"type": "result", "status": "error",
                              "error": f"Failed to install {tool_ref}"}), flush=True)
            return

        db.tools.scan_installed(db.local_tools)
        db.commit()
        db.close()

        progress_callback(100, f"{tool_ref} installé avec succès")
        print(json.dumps({"type": "result", "status": "success",
                          "message": f"Tool {tool_ref} installed successfully"}), flush=True)

    except Exception as e:
        progress_callback(100, f"Erreur : {str(e)}")
        print(json.dumps({"type": "result", "status": "error", "error": str(e)}), flush=True)


if __name__ == "__main__":
    main()
