#!/usr/bin/env python3
"""GUI helper: called by the Tauri Rust backend for DB init, pip checks, and installs."""
import sys, json, os, subprocess

HELPER_DIR = os.path.dirname(os.path.abspath(__file__))

# Production (Docker) : helper + projetclient/ dans le même dossier
prod_path = os.path.join(HELPER_DIR, "projetclient")
if os.path.isdir(prod_path):
    sys.path.insert(0, prod_path)
else:
    # Développement : helper dans projetadmin/gui-main/, projetclient à la racine du repo
    repo_root = os.path.dirname(os.path.dirname(HELPER_DIR))
    sys.path.insert(0, os.path.join(repo_root, "projetclient"))

def init_databases():
    from sql.db import ModelWeaverDB, CatalogueDB
    mw = ModelWeaverDB()
    mw._ensure_schema()
    mw.commit()
    cat = CatalogueDB()
    cat._ensure_schema()
    cat.conn.commit()
    return {"status": "ok", "mw_db": str(mw.db_path), "cat_db": str(cat.db_path)}

def check_databases():
    from sql.db import ModelWeaverDB, CatalogueDB, _default_local_db, _default_catalogue_db
    mw_path = _default_local_db()
    cat_path = _default_catalogue_db()
    result = {
        "modelweaver_db": {"path": str(mw_path), "exists": os.path.exists(mw_path)},
        "catalogue_db": {"path": str(cat_path), "exists": os.path.exists(cat_path)},
    }
    if result["modelweaver_db"]["exists"]:
        try:
            mw = ModelWeaverDB()
            count = mw.tools.list_all()
            result["modelweaver_db"]["tool_count"] = len(count)
            mw.close()
        except Exception as e:
            result["modelweaver_db"]["error"] = str(e)
    if result["catalogue_db"]["exists"]:
        try:
            cat = CatalogueDB()
            cur = cat.conn.execute("SELECT COUNT(*) FROM catalogue_providers")
            result["catalogue_db"]["provider_count"] = cur.fetchone()[0]
            cat.close()
        except Exception as e:
            result["catalogue_db"]["error"] = str(e)
    return result

def check_python_deps():
    import subprocess, sys, json as _json
    required = [
        {"name": "litellm", "module": "litellm", "type": "pip"},
        {"name": "open-webui", "module": "open_webui", "type": "pip"},
        {"name": "keyring", "module": "keyring", "type": "pip"},
        {"name": "cryptography", "module": "cryptography", "type": "pip"},
        {"name": "requests", "module": "requests", "type": "pip"},
        {"name": "psutil", "module": "psutil", "type": "pip"},
    ]
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            installed = {p["name"].lower().replace("-", "_"): p["version"] for p in _json.loads(r.stdout)}
        else:
            installed = {}
    except Exception:
        installed = {}

    for dep in required:
        key = dep["module"].lower().replace("-", "_")
        dep["installed"] = key in installed
        dep["version"] = installed.get(key)
        dep["min_version"] = {"litellm": "1.0", "open-webui": "0.1", "keyring": "23.0",
                              "cryptography": "35.0", "requests": "2.0", "psutil": "5.0"}.get(dep["name"])
    return {"deps": required}

def install_pip(package_name):
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", package_name, "--break-system-packages"],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode == 0:
        from sql.db import ModelWeaverDB
        mw = ModelWeaverDB()
        mw.scan_installed_tools()
        mw.close()
        return {"status": "ok", "log": result.stdout}
    else:
        return {"status": "error", "log": result.stderr}

def update_tools_table():
    from sql.db import ModelWeaverDB
    mw = ModelWeaverDB()
    count = mw.scan_installed_tools()
    mw.close()
    return {"status": "ok", "updated": count}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No command"}))
        sys.exit(1)
    command = sys.argv[1]
    try:
        if command == "init_databases":
            result = init_databases()
        elif command == "check_databases":
            result = check_databases()
        elif command == "check_python_deps":
            result = check_python_deps()
        elif command == "install_pip" and len(sys.argv) > 2:
            result = install_pip(sys.argv[2])
        elif command == "update_tools_table":
            result = update_tools_table()
        else:
            result = {"error": f"Unknown command: {command}"}
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
