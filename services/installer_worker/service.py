#!/usr/bin/env python3
"""Service `installer_worker` — Worker installeur : consomme install_jobs et exécute les recettes."""
import sys
import time

from services._common import log_to_file, acquire_instance_lock
from services.installer_worker import jobs


def run_installer_service():
    from services.depends import require_deps
    from pathlib import Path
    if not require_deps(Path(__file__).resolve().parent):
        sys.exit(3)
    """Worker Python (supervisé) : consomme la table install_jobs et exécute
    install_tool/uninstall_tool (recettes). Un seul processus (single-instance
    garanti par acquire_instance_lock), en pause entre deux jobs."""
    if not acquire_instance_lock("installer_worker"):
        log_to_file("WORKER", "instance déjà en cours — abandon")
        return
    log_to_file("WORKER", "installer service started")
    jobs.ensure_install_jobs()
    while True:
        try:
            row = _next_queued()
            if not row:
                time.sleep(0.5)
                continue
            job_id, ref_, name, job_type = row
            jobs._set_job(job_id, "running")
            log_to_file("WORKER", f"processing job #{job_id} {ref_} ({job_type})")

            action = uninstall_tool if job_type == "uninstall" else install_tool
            result = action(ref_)
            final = "removed" if (result.get("status") == "ok" and job_type == "uninstall") \
                else ("installed" if result.get("status") == "ok" else "failed")
            jobs._set_job(job_id, final, log=result.get("log", ""))
            log_to_file("WORKER", f"job #{job_id} -> {final}")
        except Exception as e:
            log_to_file("WORKER", f"error: {e}")
            time.sleep(1)


def _next_queued():
    import sqlite3
    from services._common import _db_paths
    mw_path, _ = _db_paths()
    con = sqlite3.connect(mw_path)
    con.row_factory = sqlite3.Row
    cur = con.execute(
        "SELECT id, ref, name, job_type FROM install_jobs WHERE status='queued' ORDER BY id LIMIT 1")
    row = cur.fetchone()
    con.close()
    return (row["id"], row["ref"], row["name"], row["job_type"]) if row else None


# Alias pour rester proche de l'ancienne API (run_installer_service appelle action).
from services.installer_worker.jobs import install_tool, uninstall_tool  # noqa: E402


if __name__ == "__main__":
    run_installer_service()
