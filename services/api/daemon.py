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
import sqlite3
import contextlib
import io
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# La logique riche vit (pour l'instant) dans projetadmin/gui-main/gui_helper.py.
# Dépendance déclarée dans _contract/dependencies.py (CONSUMES["gui_helper"]).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_GUI_MAIN = os.path.join(_REPO_ROOT, "projetadmin", "gui-main")
sys.path.insert(0, _GUI_MAIN)
import gui_helper as gh  # noqa: E402

API_VERSION = "v1"
MW_VERSION = "0.5.17"


def _mw_dir() -> Path:
    d = Path.home() / ".modelweaver"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Opérations « métier » supplémentaires (jobs list/cancel/clear, system.info) ──
# La logique lourde vit déjà dans gui_helper ; ici on ne fait qu'exposer.

def op_system_info(_params):
    return {
        "os": sys.platform,
        "system": platform.system(),
        "arch": platform.machine(),
        "home": str(Path.home()),
        "python": platform.python_version(),
    }


def op_jobs_list(_params):
    gh.ensure_install_jobs()
    mw_path, _ = gh._db_paths()
    con = sqlite3.connect(mw_path)
    con.execute("PRAGMA busy_timeout=5000")
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id,ref,name,job_type,status,log FROM install_jobs ORDER BY id").fetchall()
    con.close()
    return {"jobs": [dict(r) for r in rows], "count": len(rows)}


def op_jobs_add(params):
    ref = params.get("ref")
    job_type = params.get("job_type", "install")
    if not ref:
        return {"status": "error", "error": "missing 'ref'"}
    jid = gh._enqueue_job(ref, job_type)
    return {"status": "ok", "job_id": jid, "duplicate": jid == 0}


def op_jobs_status(params):
    jid = params.get("id")
    if jid is None:
        return {"status": "error", "error": "missing 'id'"}
    st, log = gh._job_status(int(jid))
    return {"status": "ok", "job_status": st, "log": log}


def op_jobs_cancel(params):
    jid = params.get("id")
    if jid is None:
        return {"status": "error", "error": "missing 'id'"}
    jid = int(jid)
    mw_path, _ = gh._db_paths()
    con = sqlite3.connect(mw_path)
    con.execute("PRAGMA busy_timeout=5000")
    row = con.execute(
        "SELECT pid FROM install_jobs WHERE id=? AND status='running'", (jid,)).fetchone()
    if row and row[0]:
        try:
            os.killpg(int(row[0]), 9)
        except Exception:
            try:
                os.kill(int(row[0]), 9)
            except Exception:
                pass
    con.execute(
        "UPDATE install_jobs SET status='cancelled', updated_at=strftime('%s','now') "
        "WHERE id=? AND status IN ('queued','running')", (jid,))
    con.commit()
    con.close()
    return {"status": "ok"}


def op_jobs_clear(_params):
    mw_path, _ = gh._db_paths()
    con = sqlite3.connect(mw_path)
    con.execute("PRAGMA busy_timeout=5000")
    con.execute(
        "DELETE FROM install_jobs WHERE status IN ('installed','removed','failed','cancelled')")
    con.commit()
    con.close()
    return {"status": "ok"}


def op_logs_read(_params):
    log_file = _mw_dir() / "logs" / "installer.log"
    try:
        return {"status": "ok", "log": log_file.read_text(encoding="utf-8", errors="replace")}
    except Exception as e:
        return {"status": "ok", "log": "", "note": str(e)}


def op_logs_write(params):
    gh.log_to_file(params.get("level", "INFO"), params.get("message", ""))
    return {"status": "ok"}


def _wrap(fn):
    """Adapte une fonction gui_helper sans args en handler(params)->dict, en
    silencant tout print intempestif vers stderr."""
    def handler(_params):
        with contextlib.redirect_stdout(sys.stderr):
            return fn()
    return handler


# ── Table de routage : "domaine/action" -> handler(params) -> dict ──
ROUTES = {
    # A. Système & environnement
    "system/info":            op_system_info,
    "system/deps/check":      _wrap(gh.check_python_deps),
    "system/state/get":       _wrap(gh.get_system_state),
    "system/state/save":      _wrap(gh.save_system_state),
    # B. Bases
    "db/init":                _wrap(gh.init_databases),
    "db/check":               _wrap(gh.check_databases),
    # C. Catalogue
    "catalogue/tools/list":   _wrap(gh.get_catalogue_tools),
    "catalogue/seed":         _wrap(gh.seed_catalogue),
    "catalogue/sync":         lambda p: _quiet(gh.sync_catalogue_remote, p.get("url")),
    "catalogue/tools_table/update": _wrap(gh.update_tools_table),
    # D. Outils installés (synchrone)
    "tools/installed/list":   _wrap(gh.get_installed_tools),
    "tools/install":          lambda p: _quiet(gh.install_tool, p.get("ref")),
    "tools/uninstall":        lambda p: _quiet(gh.uninstall_tool, p.get("ref")),
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()

    mw = _mw_dir()
    token = secrets.token_hex(32)
    token_file = mw / "api.token"
    token_file.write_text(token)
    os.chmod(token_file, 0o600)

    # bind avec retry (port occupé au boot)
    server = None
    port = args.port
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


if __name__ == "__main__":
    main()
