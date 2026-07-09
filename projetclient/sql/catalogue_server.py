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
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


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
        rows = self._query("SELECT * FROM catalogue_tools ORDER BY name")
        self.send_json(rows)

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
                        default=str(_project_root() / ".modelweaver" / "catalogue.db"))
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"❌ Catalogue DB introuvable : {db_path}")
        print(f"   Lance d'abord : python sql/migrate_to_sqlite.py")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    server = CatalogueServer(("0.0.0.0", args.port), CatalogueAPIHandler)
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
