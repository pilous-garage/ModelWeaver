#!/usr/bin/env python3
"""Serveur HTTP minimal pour la BDD catalogue.

Usage:
    python sql/catalogue_server.py [--port 8765] [--db .modelweaver/catalogue.db]

Endpoints:
    GET  /health
    GET  /api/providers
    GET  /api/models
    GET  /api/tools
    GET  /api/commands
"""

import json
import sqlite3
import argparse
import time
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from services._common import mw_home


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _seed_catalogue_db(db_path: Path):
    """Crée le schéma de la catalogue DB et la seed depuis tools.json (offline)."""
    import json as _json
    db_path.parent.mkdir(parents=True, exist_ok=True)
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from modules.sql.db import CatalogueDB
    cat = CatalogueDB(db_path)  # crée le schéma si absent
    data_path = root / "modules" / "catalogue" / "data" / "tools.json"
    if data_path.exists():
        with open(data_path) as f:
            rows = _json.load(f)
        cat.sync_tools(rows)
        cat.conn.commit()
    cat.close()


class CatalogueAPIHandler(BaseHTTPRequestHandler):
    conn: sqlite3.Connection = None

    def do_GET(self):
        routes = {
            "/health": self._health,
            "/api/providers": self._providers,
            "/api/models": self._models,
            "/api/tools": self._tools,
            "/api/commands": self._commands,
        }
        handler = routes.get(self.path)
        if handler:
            handler()
        else:
            self.send_response(404)
            self.send_json({"error": "not_found", "path": self.path})

    def _health(self):
        self.send_json({"status": "ok", "db": str(self.server.db_path)})

    def _providers(self):
        rows = self._query("SELECT * FROM catalogue_providers ORDER BY name")
        self.send_json(rows)

    def _models(self):
        rows = self._query("SELECT * FROM catalogue_models ORDER BY name")
        self.send_json(rows)

    def _tools(self):
        from modules.sql.db import CatalogueDB
        cat = CatalogueDB(self.server.db_path)
        result = cat.get_catalogue_tools()
        cat.close()
        self.send_json(result)

    def _commands(self):
        rows = self._query("SELECT * FROM catalogue_commands ORDER BY name")
        self.send_json(rows)

    def _query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        cur = self.server.conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def send_json(self, data):
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"  📡 {args[0]} {args[1]} → {args[2]}" if len(args) >= 3 else f"  📡 {fmt % args}")


class CatalogueServer(HTTPServer):
    allow_reuse_address = True
    db_path: Path = None
    conn: sqlite3.Connection = None


def main():
    parser = argparse.ArgumentParser(description="Catalogue API Server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=str,
                        default=str(mw_home() / "catalogue.db"))
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"⚠️  Catalogue DB absente : {db_path} — création + seed depuis tools.json")
        try:
            _seed_catalogue_db(db_path)
            print(f"✅ Catalogue DB seedée : {db_path}")
        except Exception as e:
            print(f"❌ Échec du seed de la catalogue DB : {e}")
            return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")

    server = None
    for attempt in range(10):
        try:
            server = CatalogueServer(("0.0.0.0", args.port), CatalogueAPIHandler)
            break
        except OSError as e:
            if attempt < 9:
                print(f"⚠️  Bind {args.port} échoué ({e}), retry {attempt+1}/10...")
                time.sleep(1)
            else:
                raise
    server.db_path = db_path
    server.conn = conn

    print(f"\n{'='*50}")
    print(f"  Catalogue API Server")
    print(f"  DB  : {db_path}")
    print(f"  URL : http://localhost:{args.port}")
    print(f"{'='*50}")
    print(f"  Endpoints:")
    print(f"    GET /health")
    print(f"    GET /api/providers")
    print(f"    GET /api/models")
    print(f"    GET /api/tools")
    print(f"    GET /api/commands")
    print(f"{'='*50}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Arrêt du serveur.")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    exit(main())
