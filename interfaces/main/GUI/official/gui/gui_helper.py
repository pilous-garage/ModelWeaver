#!/usr/bin/env python3
"""Compat shim Tauri -> services/modules.

Le métier a été décomposé dans services/* et modules/*. Ce fichier ne fait plus
que déléguer les commandes historiques (invoke Tauri) vers les bons modules, afin
de ne pas casser les handlers Rust existants. Les nouvelles interfaces passent
par le daemon API (services/api), et le superviseur lance services/*/service.py.
"""
import sys
import os
import json
from pathlib import Path

# Tout ce qui est imprimé par les modules (warnings, prints de debug) doit aller
# sur stderr pour ne pas polluer le JSON de résultat attendu sur stdout par le
# pont Rust (serde_json::from_str). On redirige stdout vers stderr, et on utilise
# sys.__stdout__ (le vrai stdout) uniquement pour le résultat final.
_REAL_STDOUT = sys.stdout
class _StderrMirror:
    def write(self, s):
        sys.stderr.write(s)
    def flush(self):
        sys.stderr.flush()
sys.stdout = _StderrMirror()

HELPER_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_repo_root() -> str:
    """Remonte depuis gui_helper.py jusqu'au dossier contenant services/ + modules/."""
    d = HELPER_DIR
    while True:
        if os.path.isdir(os.path.join(d, "services")) and os.path.isdir(os.path.join(d, "modules")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # MODELWEAVER_HOME explicite si défini
    env = os.environ.get("MODELWEAVER_HOME")
    if env and os.path.isdir(os.path.join(env, "services")):
        return env
    return HELPER_DIR


REPO_ROOT = _find_repo_root()
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Imports différés (appelés à l'intérieur des fonctions) pour éviter le
# démarrage lent de chaque sous-process Python. On importe en module-level
# seulement les utilitaires légers.
from services._common import _db_paths, _quiet_stdout, log_to_file


def init_databases():
    mw_path, cat_path = _db_paths()
    # Si les deux DB existent avec une taille non triviale, on suppose le
    # schéma présent (vérification rapide sans ouvrir SQLite, pour éviter
    # le blocage si le daemon tient une transaction).
    if mw_path.exists() and mw_path.stat().st_size > 1024 and cat_path.exists() and cat_path.stat().st_size > 1024:
        return {"status": "ok", "cached": True}
    from modules.sql.db import ModelWeaverDB, CatalogueDB
    mw = ModelWeaverDB(mw_path); mw.commit()
    cat = CatalogueDB(cat_path); cat.conn.commit()
    return {"status": "ok", "mw_db": str(mw.db_path), "cat_db": str(cat.db_path)}


def check_databases():
    from modules.sql.db import ModelWeaverDB, CatalogueDB
    mw_path, cat_path = _db_paths()
    result = {
        "modelweaver_db": {"path": str(mw_path), "exists": mw_path.exists()},
        "catalogue_db": {"path": str(cat_path), "exists": cat_path.exists()},
    }
    if result["modelweaver_db"]["exists"]:
        try:
            mw = ModelWeaverDB(mw_path)
            result["modelweaver_db"]["tool_count"] = len(mw.tools.list_all())
            mw.close()
        except Exception as e:
            result["modelweaver_db"]["error"] = str(e)
    if result["catalogue_db"]["exists"]:
        try:
            cat = CatalogueDB(cat_path)
            result["catalogue_db"]["provider_count"] = cat.conn.execute(
                "SELECT COUNT(*) FROM catalogue_providers").fetchone()[0]
            cat.close()
        except Exception as e:
            result["catalogue_db"]["error"] = str(e)
    return result


def check_python_deps():
    import subprocess, json as _json
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
        installed = {p["name"].lower().replace("-", "_"): p["version"]
                     for p in _json.loads(r.stdout)} if r.returncode == 0 else {}
    except Exception:
        installed = {}
    for dep in required:
        dep["installed"] = dep["module"].lower().replace("-", "_") in installed
        dep["version"] = installed.get(dep["module"].lower().replace("-", "_"))
    return {"deps": required}


def install_pip(package_name):
    from modules.sql.db import ModelWeaverDB
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", package_name, "--break-system-packages"],
        capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        mw = ModelWeaverDB(_db_paths()[0]); mw.scan_installed_tools(); mw.close()
        return {"status": "ok", "log": result.stdout}
    return {"status": "error", "log": result.stderr}


def update_tools_table():
    from modules.sql.db import ModelWeaverDB
    mw = ModelWeaverDB(_db_paths()[0]); c = mw.scan_installed_tools(); mw.close()
    return {"status": "ok", "updated": c}


def get_system_state():
    from services.watch_sysstate import service as sysstate
    return sysstate.get_system_state()


def seed_catalogue():
    _, cat_path = _db_paths()
    # Vérification sans ouvrir SQLite : si le fichier est déjà volumineux (> 1 Mo)
    # on considère qu'il est déjà peuplé (évite le blocage si daemon tient un lock).
    if cat_path.exists() and cat_path.stat().st_size > 1_048_576:
        return {"status": "ok", "seeded": False, "note": "déjà peuplé (taille)"}
    from modules.sql.db import CatalogueDB
    cat = CatalogueDB(cat_path)
    if cat.conn.execute("SELECT COUNT(*) FROM catalogue_outils").fetchone()[0] > 0:
        cat.close(); return {"status": "ok", "seeded": False, "note": "déjà peuplé"}
    data_path = os.path.join(REPO_ROOT, "modules", "catalogue", "data", "tools.json")
    with open(data_path) as f:
        rows = json.load(f)
    count = cat.sync_tools(rows)
    from services.api.daemon import seed_recipes
    seed_recipes(cat)
    cat.conn.commit(); cat.close()
    return {"status": "ok", "seeded": True, "count": count}


def get_catalogue_tools():
    import platform as _platform
    import sqlite3
    _, cat_path = _db_paths()
    os_key = _platform.system().lower()
    arch = _platform.machine().lower()
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(arch, arch)
    # Connexion directe sans passer par CatalogueDB (évite _ensure_schema
    # qui fait des DDL writes bloqués si le daemon tient un lock).
    try:
        conn = sqlite3.connect(str(cat_path), timeout=0.1)
        conn.execute("PRAGMA busy_timeout = 100")
        cur = conn.execute("""
            SELECT DISTINCT o.ref, o.nom, o.description, o.tool_type,
                   c.ref AS classe_ref, c.nom AS classe_nom,
                   r.manager, r.package, r.os, r.arch, r.confidence
            FROM catalogue_outils o
            LEFT JOIN classes_outils c ON c.classe_id = o.classe_outil_id
            JOIN catalogue_versions v ON v.outil_id = o.outil_id
            JOIN catalogue_recettes r ON r.version_id = v.version_id
            WHERE r.os IN (?, 'all') AND r.arch IN (?, 'all') AND r.enabled = 1
            ORDER BY o.nom, r.manager
        """, (os_key, arch))
        cols = [d[0] for d in cur.description]
        tools_map = {}
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            ref = d["ref"]
            if ref not in tools_map:
                tools_map[ref] = {
                    "ref": ref, "name": d["nom"],
                    "description": d["description"],
                    "tool_type": d["tool_type"],
                    "classe_ref": d["classe_ref"] or "other",
                    "classe": d["classe_nom"] or d["classe_ref"] or "other",
                    "managers": [],
                }
            tools_map[ref]["managers"].append({
                "manager": d["manager"], "package": d["package"],
                "os": d["os"], "arch": d["arch"], "confidence": d["confidence"],
            })
        conn.close()
        tools = list(tools_map.values())
        return {"tools": tools, "count": len(tools)}
    except Exception as e:
        return {"tools": [], "count": 0, "error": str(e)}


def get_installed_tools():
    import sqlite3
    mw_path, _ = _db_paths()
    try:
        conn = sqlite3.connect(str(mw_path), timeout=0.1)
        conn.execute("PRAGMA busy_timeout = 100")
        cur = conn.execute("""
            SELECT o.outil_ref, o.nom, o.version_installee, o.nom_version,
                   o.status, o.install_path, c.nom AS classe_nom, c.ref AS classe_ref
            FROM outils_installes o
            LEFT JOIN classes_outils c ON c.ref = o.classe_ref
            ORDER BY o.nom
        """)
        cols = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            out.append({
                "ref": d["outil_ref"], "name": d["nom"],
                "version": d["version_installee"] or d["nom_version"],
                "status": d["status"],
                "install_path": d["install_path"],
                "classe": d["classe_nom"], "classe_ref": d["classe_ref"],
            })
        conn.close()
        return {"tools": out, "count": len(out)}
    except Exception as e:
        return {"tools": [], "count": 0, "error": str(e)}


def save_system_state():
    from modules.sql.db import ModelWeaverDB
    from modules.checker.checker import Checker
    mw_path, _ = _db_paths()
    mw = ModelWeaverDB(mw_path); Checker().update_local_db(mw); mw.commit(); mw.close()
    return {"status": "ok"}


def get_providers():
    """Liste tous les fournisseurs du catalogue (catalogue.db)."""
    from modules.sql.db import CatalogueDB
    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    cur = cat.conn.execute(
        "SELECT ref, name, provider_type, api_type, website, is_free_tier_provider "
        "FROM catalogue_providers ORDER BY provider_type, name")
    cols = [d[0] for d in cur.description]
    providers = [dict(zip(cols, row)) for row in cur.fetchall()]
    cat.close()
    return {"providers": providers, "count": len(providers)}


def add_provider(data_json):
    """Ajoute ou met à jour un fournisseur dans catalogue.db."""
    from modules.sql.db import CatalogueDB
    import json as _json
    data = _json.loads(data_json)
    ref = data.get("ref")
    if not ref:
        return {"error": "ref requis"}
    # Types autorisés (cohérent avec catalogue_schema.sql CHECK)
    valid_types = {"cloud", "local", "ollama", "builtin"}
    ptype = data.get("provider_type", "cloud")
    if ptype not in valid_types:
        ptype = "cloud"
    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    existing = cat.conn.execute(
        "SELECT 1 FROM catalogue_providers WHERE ref=?", (ref,)).fetchone()
    cat.conn.execute("""
        INSERT INTO catalogue_providers (ref, name, provider_type, api_type, website, is_free_tier_provider)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(ref) DO UPDATE SET
            name=excluded.name, provider_type=excluded.provider_type,
            api_type=excluded.api_type, website=excluded.website,
            is_free_tier_provider=excluded.is_free_tier_provider
    """, (ref, data.get("name", ref), ptype,
          data.get("api_type"), data.get("website"),
          data.get("is_free_tier_provider", 0)))
    cat.conn.commit()
    cat.close()
    return {"status": "ok", "ref": ref, "created": existing is None}


def sync_catalogue_remote(url=None):
    from modules.sql.db import CatalogueDB
    if not url:
        url = os.environ.get("MODELWEAVER_CATALOGUE_URL", "http://localhost:8765/api")
    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    if cat.conn.execute("SELECT COUNT(*) FROM catalogue_outils").fetchone()[0] == 0:
        with _quiet_stdout():
            seed_catalogue()
    with _quiet_stdout():
        results = cat.sync_from_url(url)
    cat.close()
    return {"status": "ok", "url": url, "results": results}


def install_tool(ref):
    from services.installer_worker import jobs
    return jobs.install_tool(ref)


def uninstall_tool(ref):
    from services.installer_worker import jobs
    return jobs.uninstall_tool(ref)


def run_installer_service():
    from services.installer_worker.service import run_installer_service as _run
    _run()


def run_tester_service(script_path=None):
    from services.tester.service import run_tester_service as _run
    _run(script_path)


def watch_installed_tools(interval=2.0):
    from services.watch_installed.service import watch_installed_tools as _run
    _run(interval)


def watch_system_state(interval=2.0):
    from services.watch_sysstate.service import watch_system_state as _run
    _run(interval)


def run_agent_manager(interval=5.0):
    """Service AgentManager : supervise les threads agents."""
    from services.agent_manager.service import run_service as _run
    _run(interval)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"error": "No command"}), file=_REAL_STDOUT); _REAL_STDOUT.flush(); sys.exit(1)
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
            watch_installed_tools(); sys.exit(0)
        elif command == "watch_system_state":
            watch_system_state(); sys.exit(0)
        elif command == "run_installer_service":
            run_installer_service(); sys.exit(0)
        elif command == "run_tester_service":
            run_tester_service(); sys.exit(0)
        elif command == "run_agent_manager":
            run_agent_manager(); sys.exit(0)
        elif command == "save_system_state":
            result = save_system_state()
        elif command == "sync_catalogue_remote":
            result = sync_catalogue_remote(sys.argv[2] if len(sys.argv) > 2 else None)
        elif command == "install_tool" and len(sys.argv) > 2:
            result = install_tool(sys.argv[2]); print(json.dumps(result), file=_REAL_STDOUT); _REAL_STDOUT.flush()
            sys.exit(0 if result.get("status") == "ok" else 1)
        elif command == "uninstall_tool" and len(sys.argv) > 2:
            result = uninstall_tool(sys.argv[2]); print(json.dumps(result), file=_REAL_STDOUT); _REAL_STDOUT.flush()
            sys.exit(0 if result.get("status") == "ok" else 1)
        elif command == "get_providers":
            result = get_providers()
        elif command == "add_provider" and len(sys.argv) > 2:
            result = add_provider(sys.argv[2])
        else:
            result = {"error": f"Unknown command: {command}"}
        print(json.dumps(result), file=_REAL_STDOUT); _REAL_STDOUT.flush()
    except Exception as e:
        print(json.dumps({"error": str(e)}), file=_REAL_STDOUT); _REAL_STDOUT.flush(); sys.exit(1)
