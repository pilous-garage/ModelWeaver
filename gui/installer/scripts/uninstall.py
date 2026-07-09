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
        print(json.dumps({"type": "result", "status": "error",
                          "error": "Tool ref missing"}), flush=True)
        return

    tool_ref = sys.argv[1]
    try:
        progress_callback(0, f"Démarrage désinstallation de {tool_ref}...")
        db = ModelWeaverDB()
        tool = db.tools.get(tool_ref)
        if not tool:
            progress_callback(100, f"Outil {tool_ref} introuvable dans le catalogue")
            print(json.dumps({"type": "result", "status": "error",
                              "error": f"Tool {tool_ref} not found"}), flush=True)
            return

        # Trouver le chemin d'installation
        tool_id = db.conn.execute("SELECT id FROM tools WHERE ref = ?",
                                  (tool_ref,)).fetchone()
        install_path = None
        if tool_id:
            lt = db.conn.execute(
                "SELECT install_path FROM local_tools WHERE tool_id = ? AND status = 'installed'",
                (tool_id["id"],)).fetchone()
            if lt:
                install_path = lt["install_path"]

        installer = Installer()
        ok = installer.uninstall(tool, install_path, progress_callback)

        if ok:
            progress_callback(90, "Mise à jour de l'état local...")
            # Marquer comme désinstallé
            if tool_id:
                db.conn.execute(
                    "UPDATE local_tools SET status='uninstalled', updated_at=strftime('%s','now') WHERE tool_id=?",
                    (tool_id["id"],))
                db.commit()
            progress_callback(100, f"{tool_ref} désinstallé")
            print(json.dumps({"type": "result", "status": "success",
                              "message": f"{tool_ref} uninstalled"}), flush=True)
        else:
            progress_callback(100, f"Échec de la désinstallation de {tool_ref}")
            print(json.dumps({"type": "result", "status": "error",
                              "error": f"Failed to uninstall {tool_ref}"}), flush=True)

        db.close()

    except Exception as e:
        progress_callback(100, f"Erreur : {str(e)}")
        print(json.dumps({"type": "result", "status": "error",
                          "error": str(e)}), flush=True)


if __name__ == "__main__":
    main()
