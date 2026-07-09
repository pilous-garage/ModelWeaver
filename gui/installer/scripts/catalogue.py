import sys
import json
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from sql.db import ModelWeaverDB

def main():
    try:
        db = ModelWeaverDB()
        data = {
            "catalog": db.tools.list_all(),
            "installed": db.local_tools.list_all(),
            "classes": db.tool_classes.list_all(),
        }
        print(json.dumps({"status": "success", "data": data}))
        db.close()
    except Exception as e:
        print(json.dumps({"status": "error", "error": str(e)}))

if __name__ == "__main__":
    main()