#!/usr/bin/env python3
"""File d'installation + opérations d'install/désinstall réelles.

Extraits de l'ancien gui_helper pour devenir la source de vérité du service
`installer_worker` (et consommés directement par le daemon API). La concurrence
est gérée par SQLite (WAL + busy_timeout) ; voir CARNET (RBAC data-layer).
"""
import sys
import os
import json
import sqlite3
from pathlib import Path

from services._common import _db_paths, _quiet_stdout, RECIPE_BASE, runtime_db_path

BUSY_TIMEOUT = 5000

_shared_conn = None

def set_shared_conn(conn):
    global _shared_conn
    _shared_conn = conn


def _conn():
    if _shared_conn is not None:
        return _shared_conn
    con = sqlite3.connect(runtime_db_path())
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _close(con):
    if _shared_conn is None:
        con.close()


# ──────────────────────────────────────────────
#  File install_jobs
# ──────────────────────────────────────────────

def ensure_install_jobs() -> None:
    con = _conn()
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
    _close(con)


def enqueue_job(ref: str, job_type: str) -> int:
    """Enfile un job (idempotent: skip si déjà actif pour ce ref).
    Retourne l'id du job créé, ou 0 si doublon."""
    con = _conn()
    cur = con.execute(
        "SELECT COUNT(*) FROM install_jobs WHERE ref=? AND status IN ('queued','running')", (ref,))
    if cur.fetchone()[0] > 0:
        _close(con)
        return 0
    con.execute(
        "INSERT INTO install_jobs (ref,name,job_type,status,created_at,updated_at) "
        "VALUES (?,?,?,?,strftime('%s','now'),strftime('%s','now'))",
        (ref, ref, job_type, "queued"))
    jid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    _close(con)
    return jid


def job_status(jid: int):
    con = _conn()
    cur = con.execute("SELECT status, log FROM install_jobs WHERE id=?", (jid,))
    row = cur.fetchone()
    _close(con)
    return (row[0] if row else None, row[1] if row else "")


def list_jobs():
    con = _conn()
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id,ref,name,job_type,status,log FROM install_jobs ORDER BY id").fetchall()
    _close(con)
    return {"jobs": [dict(r) for r in rows], "count": len(rows)}


def cancel_job(jid: int) -> None:
    con = _conn()
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
    _close(con)


def clear_jobs() -> None:
    con = _conn()
    con.execute(
        "DELETE FROM install_jobs WHERE status IN ('installed','removed','failed','cancelled')")
    con.commit()
    _close(con)


def _set_job(job_id: int, status: str, log=None, pid=None) -> None:
    con = _conn()
    if pid is not None:
        con.execute("UPDATE install_jobs SET status=?, log=?, pid=?, updated_at=strftime('%s','now') WHERE id=?",
                    (status, log, pid, job_id))
    elif log is not None:
        con.execute("UPDATE install_jobs SET status=?, log=?, updated_at=strftime('%s','now') WHERE id=?",
                    (status, log, job_id))
    else:
        con.execute("UPDATE install_jobs SET status=?, updated_at=strftime('%s','now') WHERE id=?",
                    (status, job_id))
    con.commit()
    _close(con)


# ──────────────────────────────────────────────
#  Install / Uninstall réels (recettes)
# ──────────────────────────────────────────────

def install_tool(ref: str, mw_shared=None, cat_shared=None) -> dict:
    from modules.sql.db import CatalogueDB, ModelWeaverDB
    from modules.installer.installer import Installer

    if cat_shared is not None:
        cur = cat_shared.conn.execute(
            "SELECT ref, name, description, tool_type, install_method, current_version, "
            "default_download_url, allowed_platforms, allowed_arches FROM catalogue_tools WHERE ref = ?",
            (ref,))
        row = cur.fetchone()
    else:
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

    log_path = RECIPE_BASE.parent.parent / f"install_{ref}.log"
    log_lines = []

    def progress(pct, msg):
        line = f"[{pct}%] {msg}"
        log_lines.append(line)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    installer = Installer(project_root=str(RECIPE_BASE))
    with _quiet_stdout():
        ok = installer.install(tool, progress_callback=progress)
    if ok:
        if mw_shared is not None:
            mw_shared.scan_installed_tools()
        else:
            mw = ModelWeaverDB(_db_paths()[0])
            mw.scan_installed_tools()
            mw.close()
    return {
        "status": "ok" if ok else "error",
        "ref": ref,
        "log": "\n".join(log_lines),
    }


def uninstall_tool(ref: str, mw_shared=None, cat_shared=None) -> dict:
    from modules.sql.db import CatalogueDB, ModelWeaverDB
    from modules.installer.installer import Installer

    if cat_shared is not None:
        cur = cat_shared.conn.execute(
            "SELECT ref, name, description, tool_type, install_method, current_version, "
            "default_download_url, allowed_platforms, allowed_arches FROM catalogue_tools WHERE ref = ?",
            (ref,))
        row = cur.fetchone()
    else:
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

    log_path = RECIPE_BASE.parent.parent / f"uninstall_{ref}.log"
    log_lines = []

    def progress(pct, msg):
        line = f"[{pct}%] {msg}"
        log_lines.append(line)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    installer = Installer(project_root=str(RECIPE_BASE))
    with _quiet_stdout():
        ok = installer.uninstall(tool, progress_callback=progress)
    if ok:
        if mw_shared is not None:
            mw_shared.conn.execute(
                "DELETE FROM local_tools WHERE tool_id = (SELECT id FROM tool_definitions WHERE ref = ?)",
                (ref,))
            mw_shared.conn.commit()
            mw_shared.scan_installed_tools()
        else:
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
