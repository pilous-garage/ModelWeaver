import sys
import json
from pathlib import Path

# Add root to path to import modules
sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from modules.installer.installer import Installer
from sql.db import ModelWeaverDB

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "error": "Tool ref missing"}))
        return

    tool_ref = sys.argv[1]
    try:
        db = ModelWeaverDB()
        tool = db.tools.get(tool_ref)
        if not tool:
            print(json.dumps({"status": "error", "error": f"Tool {tool_ref} not found in catalog"}))
            return

        installer = Installer()
        success = installer.install(tool)
        
        # Update local state
        db.tools.scan_installed(db.local_tools)
        db.commit()
        db.close()

        if success:
            print(json.dumps({"status": "success", "message": f"Tool {tool_ref} installed successfully"}))
        else:
            print(json.dumps({"status": "error", "error": f"Failed to install {tool_ref}"}))

    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))

if __name__ == "__main__":
    main()
