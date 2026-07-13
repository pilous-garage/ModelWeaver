#!/usr/bin/env python3
"""Service `watch_installed` — scanne périodiquement les outils installés (cache état)."""
import sys
import time
import json

from services._common import acquire_instance_lock, _db_paths
from modules.sql.db import ModelWeaverDB


def watch_installed_tools(interval: float = 2.0):
    """Boucle : scan les outils installés et écrit un JSON par ligne sur stdout
    (une ligne = un état). Un seul processus (single-instance), en pause."""
    if not acquire_instance_lock("watch_installed"):
        return
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


if __name__ == "__main__":
    watch_installed_tools()
