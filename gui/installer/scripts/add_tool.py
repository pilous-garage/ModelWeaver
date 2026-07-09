import sys
import json
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent.parent.parent))

from sql.db import ModelWeaverDB


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "error": "Tool JSON missing"}), flush=True)
        return

    try:
        data = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "error": f"Invalid JSON: {e}"}), flush=True)
        return

    required = ["ref", "name"]
    for field in required:
        if field not in data or not data[field]:
            print(json.dumps({"status": "error",
                              "error": f"Missing required field: {field}"}), flush=True)
            return

    db = ModelWeaverDB()

    existing = db.conn.execute("SELECT id FROM tools WHERE ref = ?", (data["ref"],)).fetchone()
    if existing:
        db.close()
        print(json.dumps({"status": "error",
                          "error": f"Tool '{data['ref']}' already exists"}), flush=True)
        return

    tool_id = db.tools.save({
        "ref": data["ref"],
        "name": data["name"],
        "description": data.get("description", ""),
        "tool_type": data.get("tool_type", "binary"),
        "install_method": data.get("install_method", "direct-url"),
        "current_version": data.get("current_version"),
        "recipe_path": data.get("recipe_path"),
        "default_download_url": data.get("default_download_url"),
        "class": data.get("class", "other"),
    })

    db.commit()
    db.close()

    print(json.dumps({"status": "success",
                      "data": {"id": tool_id, "ref": data["ref"]}}), flush=True)


if __name__ == "__main__":
    main()