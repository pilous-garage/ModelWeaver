#!/usr/bin/env python3
"""GUI helper: called by the Tauri Rust backend for DB init, pip checks, and installs."""
import sys, json, os, subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

HELPER_DIR = os.path.dirname(os.path.abspath(__file__))

# Production (Docker) : helper + projetclient/ dans le même dossier
prod_path = os.path.join(HELPER_DIR, "projetclient")
if os.path.isdir(prod_path):
    sys.path.insert(0, prod_path)
    PROJETCLIENT_DIR = prod_path
else:
    # Développement : helper dans projetadmin/gui-main/, projetclient à la racine du repo
    repo_root = os.path.dirname(os.path.dirname(HELPER_DIR))
    sys.path.insert(0, os.path.join(repo_root, "projetclient"))
    PROJETCLIENT_DIR = os.path.join(repo_root, "projetclient")

# RecipeParser.join("install_recipe") -> on vise projetclient/modules/installer
RECIPE_BASE = os.path.join(PROJETCLIENT_DIR, "modules", "installer")


def _db_paths() -> tuple[Path, Path]:
    """Chemins DB stables sous ~/.modelweaver (indépendants du CWD)."""
    home = Path.home()
    mw_dir = home / ".modelweaver"
    mw_dir.mkdir(parents=True, exist_ok=True)
    return mw_dir / "modelweaver.db", mw_dir / "catalogue.db"


import contextlib
import io

@contextlib.contextmanager
def _quiet_stdout():
    """Redirige stdout vers stderr le temps d'un appel qui print (sync/install)."""
    old = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = old


def init_databases():
    from sql.db import ModelWeaverDB, CatalogueDB
    mw_path, cat_path = _db_paths()
    mw = ModelWeaverDB(mw_path)
    mw._ensure_schema()
    mw.commit()
    cat = CatalogueDB(cat_path)
    cat._ensure_schema()
    cat.conn.commit()
    return {"status": "ok", "mw_db": str(mw.db_path), "cat_db": str(cat.db_path)}


def check_databases():
    from sql.db import ModelWeaverDB, CatalogueDB, _default_local_db, _default_catalogue_db
    mw_path, cat_path = _db_paths()
    result = {
        "modelweaver_db": {"path": str(mw_path), "exists": mw_path.exists()},
        "catalogue_db": {"path": str(cat_path), "exists": cat_path.exists()},
    }
    if result["modelweaver_db"]["exists"]:
        try:
            mw = ModelWeaverDB(mw_path)
            count = mw.tools.list_all()
            result["modelweaver_db"]["tool_count"] = len(count)
            mw.close()
        except Exception as e:
            result["modelweaver_db"]["error"] = str(e)
    if result["catalogue_db"]["exists"]:
        try:
            cat = CatalogueDB(cat_path)
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
        mw = ModelWeaverDB(_db_paths()[0])
        mw.scan_installed_tools()
        mw.close()
        return {"status": "ok", "log": result.stdout}
    else:
        return {"status": "error", "log": result.stderr}


def update_tools_table():
    from sql.db import ModelWeaverDB
    mw = ModelWeaverDB(_db_paths()[0])
    count = mw.scan_installed_tools()
    mw.close()
    return {"status": "ok", "updated": count}


# ── Logithèque ──

def _ensure_psutil():
    try:
        import psutil  # noqa: F401
        return True
    except Exception:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "psutil",
                             "--break-system-packages"], capture_output=True, text=True, timeout=120)
            import psutil  # noqa: F401
            return True
        except Exception:
            return False


def get_system_state():
    from modules.checker.checker import Checker
    checker = Checker()
    info = checker.get_system_info()
    managers = checker.get_detected_managers()
    state = {
        "os": info.get("os"),
        "os_version": info.get("os_version"),
        "architecture": info.get("architecture"),
        "processor": info.get("processor"),
        "detected_managers": managers,
    }
    if _ensure_psutil():
        hw = checker.get_hardware_info()
        state.update(hw)
    else:
        state.update({
            "ram_total_gb": None, "ram_available_gb": None,
            "disk_total_gb": None, "disk_free_gb": None,
        })
    return state


def seed_catalogue():
    from sql.db import CatalogueDB
    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    cur = cat.conn.execute("SELECT COUNT(*) FROM catalogue_tools")
    if cur.fetchone()[0] > 0:
        cat.close()
        return {"status": "ok", "seeded": False, "note": "catalogue already populated"}
    data_path = os.path.join(HELPER_DIR, "projetclient", "modules", "catalogue", "data", "tools.json")
    with open(data_path) as f:
        rows = json.load(f)
    count = cat.sync_tools(rows)
    cat.conn.commit()
    cat.close()
    return {"status": "ok", "seeded": True, "count": count}


def get_catalogue_tools():
    from sql.db import CatalogueDB
    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    cur = cat.conn.execute(
        "SELECT ref, name, description, tool_type, install_method, current_version, allowed_platforms, allowed_arches "
        "FROM catalogue_tools ORDER BY name")
    cols = [d[0] for d in cur.description]
    tools = [dict(zip(cols, row)) for row in cur.fetchall()]
    cat.close()
    return {"tools": tools, "count": len(tools)}


def get_installed_tools():
    from sql.db import ModelWeaverDB
    mw_path, _ = _db_paths()
    mw = ModelWeaverDB(mw_path)
    rows = mw.local_tools.list_all()
    out = []
    for r in rows:
        out.append({
            "ref": r.get("tool_ref") or r.get("ref"),
            "name": r.get("tool_name") or r.get("name"),
            "version": r.get("version"),
            "status": r.get("status"),
            "install_path": r.get("install_path"),
        })
    mw.close()
    return {"tools": out, "count": len(out)}


def watch_installed_tools(interval: float = 2.0):
    """Service loop: scan les outils installés en boucle et écrit un JSON par
    ligne sur stdout (une seule ligne = un état). Le superviseur lit stdout
    et met en cache. Un seul processus, en pause (sleep) entre deux scans."""
    import time
    from sql.db import ModelWeaverDB
    mw_path, _ = _db_paths()
    while True:
        try:
            mw = ModelWeaverDB(mw_path)
            rows = mw.local_tools.list_all()
            out = [{
                "ref": r.get("tool_ref") or r.get("ref"),
                "name": r.get("tool_name") or r.get("name"),
                "version": r.get("version"),
                "status": r.get("status"),
                "install_path": r.get("install_path"),
            } for r in rows]
            mw.close()
            print(json.dumps({"tools": out, "count": len(out)}), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)
        time.sleep(interval)


def save_system_state():
    from sql.db import ModelWeaverDB
    from modules.checker.checker import Checker
    mw_path, _ = _db_paths()
    mw = ModelWeaverDB(mw_path)
    Checker().update_local_db(mw)
    mw.commit()
    mw.close()
    return {"status": "ok"}


def watch_system_state(interval: float = 2.0):
    """Service loop: met à jour system_state puis écrit l'état courant en JSON
    sur stdout (une ligne = un état). Un seul processus, en pause entre scans."""
    import time
    while True:
        try:
            save_system_state()
            state = get_system_state()
            print(json.dumps(state), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)
        time.sleep(interval)


def log_to_file(level, message):
    try:
        from datetime import datetime
        log_dir = _db_paths()[0].parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "installer.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat()} [{level}] {message}\n")
    except Exception:
        pass


def ensure_install_jobs():
    import sqlite3
    mw_path, _ = _db_paths()
    con = sqlite3.connect(mw_path)
    con.execute("PRAGMA busy_timeout=5000")
    con.execute("""CREATE TABLE IF NOT EXISTS install_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ref TEXT NOT NULL,
        name TEXT,
        job_type TEXT,
        status TEXT,
        log TEXT,
        pid INTEGER,
        created_at INTEGER,
        updated_at INTEGER DEFAULT (strftime('%s','now'))
    );""")
    con.commit()
    con.close()


def _set_job(job_id, status, log=None, pid=None):
    import sqlite3
    mw_path, _ = _db_paths()
    con = sqlite3.connect(mw_path)
    con.execute("PRAGMA busy_timeout=5000")
    if pid is not None:
        con.execute("UPDATE install_jobs SET status=?, log=?, pid=?, updated_at=strftime('%s','now') WHERE id=?", (status, log, pid, job_id))
    elif log is not None:
        con.execute("UPDATE install_jobs SET status=?, log=?, updated_at=strftime('%s','now') WHERE id=?", (status, log, job_id))
    else:
        con.execute("UPDATE install_jobs SET status=?, updated_at=strftime('%s','now') WHERE id=?", (status, job_id))
    con.commit()
    con.close()


def run_installer_service():
    """Worker Python (supervisé par Rust) : consomme la table install_jobs et
    exécute install_tool/uninstall_tool (recettes). Un seul processus, en pause
    entre deux jobs."""
    import time, sqlite3
    log_to_file("WORKER", "installer service started")
    ensure_install_jobs()
    while True:
        try:
            mw_path, _ = _db_paths()
            con = sqlite3.connect(mw_path)
            con.row_factory = sqlite3.Row
            cur = con.execute("SELECT id, ref, name, job_type FROM install_jobs WHERE status='queued' ORDER BY id LIMIT 1")
            row = cur.fetchone()
            con.close()
            if not row:
                time.sleep(0.5)
                continue
            job_id, ref_, name, job_type = row["id"], row["ref"], row["name"], row["job_type"]
            _set_job(job_id, "running")
            log_to_file("WORKER", f"processing job #{job_id} {ref_} ({job_type})")

            # Pré-check séquentiel (informatif, jamais bloquant)
            try:
                pre = check_python_deps()
                missing = [d["name"] for d in pre.get("deps", []) if not d.get("installed")]
                if missing:
                    log_to_file("PRECHECK", f"deps manquantes: {missing}")
            except Exception as e:
                log_to_file("PRECHECK", f"erreur: {e}")

            action = "uninstall_tool" if job_type == "uninstall" else "install_tool"
            proc = subprocess.Popen([sys.executable, __file__, action, ref_],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _set_job(job_id, "running", pid=proc.pid)
            sout, serr = proc.communicate()
            success = proc.returncode == 0
            log = sout.decode(errors="replace")
            if not success:
                log = log + "\n" + serr.decode(errors="replace")
            final = "removed" if (success and job_type == "uninstall") else ("installed" if success else "failed")
            _set_job(job_id, final, log=log)
            log_to_file("WORKER", f"job #{job_id} -> {'ok' if success else 'fail'}")
        except Exception as e:
            log_to_file("WORKER", f"error: {e}")
            time.sleep(1)


def sync_catalogue_remote(url=None):
    from sql.db import CatalogueDB
    if not url:
        url = os.environ.get("MODELWEAVER_CATALOGUE_URL", "http://localhost:8765/api")
    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    # S'assurer qu'un catalogue de base existe (offline)
    cur = cat.conn.execute("SELECT COUNT(*) FROM catalogue_tools")
    if cur.fetchone()[0] == 0:
        with _quiet_stdout():
            seed_catalogue()
    with _quiet_stdout():
        results = cat.sync_from_url(url)
    cat.close()
    return {"status": "ok", "url": url, "results": results}


def install_tool(ref):
    from sql.db import CatalogueDB, ModelWeaverDB
    from modules.installer.installer import Installer

    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    cur = cat.conn.execute(
        "SELECT ref, name, description, tool_type, install_method, current_version, "
        "default_download_url, allowed_platforms, allowed_arches FROM catalogue_tools WHERE ref = ?",
        (ref,))
    row = cur.fetchone()
    cat.close()
    if not row:
        return {"status": "error", "log": f"Outil inconnu: {ref}"}
    cols = ["ref", "name", "description", "tool_type", "install_method", "current_version",
            "default_download_url", "allowed_platforms", "allowed_arches"]
    tool = dict(zip(cols, row))

    log_path = os.path.join(HELPER_DIR, f"install_{ref}.log")
    log_lines = []
    def progress(pct, msg):
        line = f"[{pct}%] {msg}"
        log_lines.append(line)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    installer = Installer(project_root=RECIPE_BASE)
    with _quiet_stdout():
        ok = installer.install(tool, progress_callback=progress)
    if ok:
        mw = ModelWeaverDB(_db_paths()[0])
        mw.scan_installed_tools()
        mw.close()
    return {
        "status": "ok" if ok else "error",
        "ref": ref,
        "log": "\n".join(log_lines),
    }


def uninstall_tool(ref):
    from sql.db import CatalogueDB, ModelWeaverDB
    from modules.installer.installer import Installer

    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    cur = cat.conn.execute(
        "SELECT ref, name, description, tool_type, install_method, current_version, "
        "default_download_url, allowed_platforms, allowed_arches FROM catalogue_tools WHERE ref = ?",
        (ref,))
    row = cur.fetchone()
    cat.close()
    if not row:
        return {"status": "error", "log": f"Outil inconnu: {ref}"}
    cols = ["ref", "name", "description", "tool_type", "install_method", "current_version",
            "default_download_url", "allowed_platforms", "allowed_arches"]
    tool = dict(zip(cols, row))

    log_path = os.path.join(HELPER_DIR, f"uninstall_{ref}.log")
    log_lines = []
    def progress(pct, msg):
        line = f"[{pct}%] {msg}"
        log_lines.append(line)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    installer = Installer(project_root=RECIPE_BASE)
    with _quiet_stdout():
        ok = installer.uninstall(tool, progress_callback=progress)
    if ok:
        mw = ModelWeaverDB(_db_paths()[0])
        mw.conn.execute(
            "DELETE FROM local_tools WHERE tool_id = (SELECT id FROM tool_definitions WHERE ref = ?)",
            (ref,))
        mw.commit()
        mw.scan_installed_tools()
        mw.close()
    return {
        "status": "ok" if ok else "error",
        "ref": ref,
        "log": "\n".join(log_lines),
    }


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
        elif command == "get_system_state":
            result = get_system_state()
        elif command == "seed_catalogue":
            result = seed_catalogue()
        elif command == "get_catalogue_tools":
            result = get_catalogue_tools()
        elif command == "get_installed_tools":
            result = get_installed_tools()
        elif command == "watch_installed_tools":
            watch_installed_tools()
        elif command == "watch_system_state":
            watch_system_state()
        elif command == "run_installer_service":
            run_installer_service()
        elif command == "save_system_state":
            result = save_system_state()
        elif command == "sync_catalogue_remote":
            result = sync_catalogue_remote(sys.argv[2] if len(sys.argv) > 2 else None)
        elif command == "install_tool" and len(sys.argv) > 2:
            result = install_tool(sys.argv[2])
            print(json.dumps(result))
            sys.exit(0 if result.get("status") == "ok" else 1)
        elif command == "uninstall_tool" and len(sys.argv) > 2:
            result = uninstall_tool(sys.argv[2])
            print(json.dumps(result))
            sys.exit(0 if result.get("status") == "ok" else 1)
        else:
            result = {"error": f"Unknown command: {command}"}
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
