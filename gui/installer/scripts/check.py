import sys
import json
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from modules.checker.checker import Checker
from sql.db import ModelWeaverDB

def main():
    try:
        checker = Checker()
        state = checker.run_all_checks()

        db = ModelWeaverDB()
        installed_tools = db.local_tools.list_all(status="installed")
        db.close()

        state["tools_installed"] = installed_tools

        print(json.dumps({"status": "success", "data": state}))
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))

if __name__ == "__main__":
    main()