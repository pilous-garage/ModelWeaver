#!/usr/bin/env python3
"""
ModelWeaver — Daemon API locale.

Backend unique, indépendant de toute GUI. Expose sur 127.0.0.1 (HTTP/JSON) les
opérations « accessibles depuis l'externe » définies dans ARCHITECTURE_API.md.
Toute interface (GUI Tauri v1/v2, CLI, web, TUI) est un simple client.

Sécurité :
  - bind STRICT sur 127.0.0.1 (jamais 0.0.0.0)
  - token de session écrit dans ~/.modelweaver/api.token (perms 600), exigé via
    l'en-tête `Authorization: Bearer <token>` pour toute route /v1/*.

Découverte par les clients :
  - ~/.modelweaver/api.port  : port courant
  - ~/.modelweaver/api.token : token de session

Usage:
  python mw_daemon.py [--port 8770]
"""
import sys
import os
import json
import time
import secrets
import argparse
import platform
import contextlib
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# Le daemon est le backend unique et indépendant de toute GUI. Il consomme
# directement les modules (source de vérité) et le service installer_worker
# (file de jobs + install/uninstall). Aucune dépendance à gui_helper.
from services.installer_worker import jobs
from services.watch_sysstate import service as sysstate
from modules.sql.db import ModelWeaverDB, CatalogueDB
from modules.checker.checker import Checker
from services._common import _db_paths, _quiet_stdout, log_to_file

API_VERSION = "v1"
MW_VERSION = "0.5.20"


def _mw_dir() -> Path:
    d = Path.home() / ".modelweaver"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Opérations « métier » : implémentées ici en consommant modules/services ──

def op_system_info(_params):
    return {
        "os": sys.platform,
        "system": platform.system(),
        "arch": platform.machine(),
        "home": str(Path.home()),
        "python": platform.python_version(),
    }


def check_python_deps():
    """Vérifie les dépendances pip requises (extrait de l'ancien gui_helper)."""
    import subprocess
    import json as _json
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
        key = dep["module"].lower().replace("-", "_")
        dep["installed"] = key in installed
        dep["version"] = installed.get(key)
        dep["min_version"] = {"litellm": "1.0", "open-webui": "0.1", "keyring": "23.0",
                              "cryptography": "35.0", "requests": "2.0", "psutil": "5.0"}.get(dep["name"])
    return {"deps": required}


def init_databases():
    mw_path, cat_path = _db_paths()
    mw = ModelWeaverDB(mw_path)
    mw._ensure_schema()
    mw.commit()
    cat = CatalogueDB(cat_path)
    cat._ensure_schema()
    cat.conn.commit()
    return {"status": "ok", "mw_db": str(mw.db_path), "cat_db": str(cat.db_path)}


def check_databases():
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
            cur = cat.conn.execute("SELECT COUNT(*) FROM catalogue_providers")
            result["catalogue_db"]["provider_count"] = cur.fetchone()[0]
            cat.close()
        except Exception as e:
            result["catalogue_db"]["error"] = str(e)
    return result


def seed_catalogue():
    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    cur = cat.conn.execute("SELECT COUNT(*) FROM catalogue_tools")
    if cur.fetchone()[0] > 0:
        cat.close()
        return {"status": "ok", "seeded": False, "note": "catalogue already populated"}
    data_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "modules", "catalogue", "data", "tools.json")
    with open(data_path) as f:
        rows = json.load(f)
    count = cat.sync_tools(rows)
    cat.conn.commit()
    cat.close()
    return {"status": "ok", "seeded": True, "count": count}


def get_catalogue_tools():
    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    cur = cat.conn.execute(
        "SELECT ref, name, description, tool_type, install_method, current_version, "
        "allowed_platforms, allowed_arches FROM catalogue_tools ORDER BY name")
    cols = [d[0] for d in cur.description]
    tools = [dict(zip(cols, row)) for row in cur.fetchall()]
    cat.close()
    return {"tools": tools, "count": len(tools)}


def get_installed_tools():
    mw_path, _ = _db_paths()
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
    return {"tools": out, "count": len(out)}


def save_system_state():
    mw_path, _ = _db_paths()
    mw = ModelWeaverDB(mw_path)
    Checker().update_local_db(mw)
    mw.commit()
    mw.close()
    return {"status": "ok"}


def sync_catalogue_remote(url=None):
    if not url:
        url = os.environ.get("MODELWEAVER_CATALOGUE_URL", "http://localhost:8765/api")
    _, cat_path = _db_paths()
    cat = CatalogueDB(cat_path)
    if cat.conn.execute("SELECT COUNT(*) FROM catalogue_tools").fetchone()[0] == 0:
        with _quiet_stdout():
            seed_catalogue()
    with _quiet_stdout():
        results = cat.sync_from_url(url)
    cat.close()
    return {"status": "ok", "url": url, "results": results}


def update_tools_table():
    mw_path, _ = _db_paths()
    mw = ModelWeaverDB(mw_path)
    count = mw.scan_installed_tools()
    mw.close()
    return {"status": "ok", "updated": count}


def op_jobs_list(_params):
    return jobs.list_jobs()


def op_jobs_add(params):
    ref = params.get("ref")
    job_type = params.get("job_type", "install")
    if not ref:
        return {"status": "error", "error": "missing 'ref'"}
    jid = jobs.enqueue_job(ref, job_type)
    return {"status": "ok", "job_id": jid, "duplicate": jid == 0}


def op_jobs_status(params):
    jid = params.get("id")
    if jid is None:
        return {"status": "error", "error": "missing 'id'"}
    st, log = jobs.job_status(int(jid))
    return {"status": "ok", "job_status": st, "log": log}


def op_jobs_cancel(params):
    jid = params.get("id")
    if jid is None:
        return {"status": "error", "error": "missing 'id'"}
    jobs.cancel_job(int(jid))
    return {"status": "ok"}


def op_jobs_clear(_params):
    jobs.clear_jobs()
    return {"status": "ok"}


def op_logs_read(_params):
    log_file = _mw_dir() / "logs" / "installer.log"
    try:
        return {"status": "ok", "log": log_file.read_text(encoding="utf-8", errors="replace")}
    except Exception as e:
        return {"status": "ok", "log": "", "note": str(e)}


def op_logs_write(params):
    log_to_file(params.get("level", "INFO"), params.get("message", ""))
    return {"status": "ok"}


def _wrap(fn):
    """Adapte une fonction sans args en handler(params)->dict, en silençant
    tout print intempestif vers stderr."""
    def handler(_params):
        with contextlib.redirect_stdout(sys.stderr):
            return fn()
    return handler


# ── Table de routage : "domaine/action" -> handler(params) -> dict ──
ROUTES = {
    # A. Système & environnement
    "system/info":            op_system_info,
    "system/deps/check":      _wrap(check_python_deps),
    "system/state/get":       _wrap(sysstate.get_system_state),
    "system/state/save":      _wrap(save_system_state),
    # B. Bases
    "db/init":                _wrap(init_databases),
    "db/check":               _wrap(check_databases),
    # C. Catalogue
    "catalogue/tools/list":   _wrap(get_catalogue_tools),
    "catalogue/seed":         _wrap(seed_catalogue),
    "catalogue/sync":         lambda p: _quiet(sync_catalogue_remote, p.get("url")),
    "catalogue/tools_table/update": _wrap(update_tools_table),
    # D. Outils installés (synchrone)
    "tools/installed/list":   _wrap(get_installed_tools),
    "tools/install":          lambda p: _quiet(jobs.install_tool, p.get("ref")),
    "tools/uninstall":        lambda p: _quiet(jobs.uninstall_tool, p.get("ref")),
    # E. File de jobs (asynchrone)
    "jobs/add":               op_jobs_add,
    "jobs/list":              op_jobs_list,
    "jobs/status":            op_jobs_status,
    "jobs/cancel":            op_jobs_cancel,
    "jobs/clear":             op_jobs_clear,
    # H. Logs
    "logs/read":              op_logs_read,
    "logs/write":             op_logs_write,
}


def _quiet(fn, *args):
    with contextlib.redirect_stdout(sys.stderr):
        if any(a is None for a in args):
            return fn()
        return fn(*args)


class MWAPIHandler(BaseHTTPRequestHandler):
    server_version = "ModelWeaverDaemon/1.0"

    def log_message(self, *args):
        pass  # silence le logging par défaut sur stderr

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        return secrets.compare_digest(auth, expected)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "version": MW_VERSION, "api": API_VERSION})
            return
        self._send(404, {"error": "not_found", "path": self.path})

    def do_POST(self):
        prefix = f"/{API_VERSION}/"
        if not self.path.startswith(prefix):
            self._send(404, {"error": "not_found", "path": self.path})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        route = self.path[len(prefix):].strip("/")
        handler = ROUTES.get(route)
        if not handler:
            self._send(404, {"error": "unknown_route", "route": route})
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            params = json.loads(raw) if raw else {}
        except Exception as e:
            self._send(400, {"error": "bad_request", "detail": str(e)})
            return
        try:
            result = handler(params)
            self._send(200, {"ok": True, "route": route, "result": result})
        except Exception as e:
            import traceback
            self._send(500, {"ok": False, "route": route, "error": str(e),
                             "trace": traceback.format_exc()})


def serve(port: int = 8770) -> None:
    """Point d'entrée du service `api` (supervisé). Un seul daemon à la fois."""
    from services._common import acquire_instance_lock
    if not acquire_instance_lock("api"):
        print("❌ daemon déjà en cours (lock api)", file=sys.stderr)
        sys.exit(1)

    mw = _mw_dir()
    token = secrets.token_hex(32)
    token_file = mw / "api.token"
    token_file.write_text(token)
    os.chmod(token_file, 0o600)

    # bind avec retry (port occupé au boot)
    server = None
    for attempt in range(10):
        try:
            server = HTTPServer(("127.0.0.1", port), MWAPIHandler)
            break
        except OSError as e:
            print(f"⚠️  bind {port} échoué ({e}), retry {attempt + 1}/10...", file=sys.stderr)
            time.sleep(1)
    if server is None:
        print("❌ impossible de binder le daemon", file=sys.stderr)
        sys.exit(1)

    server.token = token
    (mw / "api.port").write_text(str(port))

    print(f"✅ ModelWeaver daemon — http://127.0.0.1:{port}  (api {API_VERSION})", file=sys.stderr)
    print(f"   token : {token_file}", file=sys.stderr)
    print(f"   routes: {len(ROUTES)}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("arrêt du daemon", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    serve(args.port)


if __name__ == "__main__":
    main()
