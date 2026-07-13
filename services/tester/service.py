#!/usr/bin/env python3
"""Service `tester` — lit un script d'actions install/uninstall et enfile les jobs."""
import sys
import time
from pathlib import Path

from services._common import log_to_file, acquire_instance_lock, _db_paths
from services.installer_worker import jobs


def run_tester_service(script_path=None):
    from services.depends import require_deps
    from pathlib import Path
    if not require_deps(Path(__file__).resolve().parent):
        sys.exit(3)
    """Service testeur (supervisé) : lit un fichier script (.txt) listant des
    actions (install <ref> / uninstall <ref>) et les enfile dans install_jobs.
    Le worker installer consomme la file séquentiellement. Rapport écrit dans
    ~/.modelweaver/tests/test-report.txt puis le script est archivé en .done.txt."""
    if not acquire_instance_lock("tester"):
        log_to_file("TESTER", "instance déjà en cours — abandon")
        return
    log_to_file("TESTER", "tester service started")
    tests_dir = _db_paths()[0].parent / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    script = Path(script_path) if script_path else (tests_dir / "test-script.txt")
    while True:
        try:
            if script.exists():
                log_to_file("TESTER", f"script found: {script}")
                actions = []
                for ln in script.read_text().splitlines():
                    s = ln.strip()
                    if not s or s.startswith("#"):
                        continue
                    parts = s.split()
                    cmd = parts[0].lower()
                    if cmd in ("install", "uninstall", "remove") and len(parts) >= 2:
                        ref = parts[1]
                        jt = "uninstall" if cmd in ("uninstall", "remove") else "install"
                        actions.append((jt, ref))
                log_to_file("TESTER", f"{len(actions)} actions parsed")
                report = [
                    "=== ModelWeaver — Test Report ===",
                    f"script : {script}",
                    f"date   : {time.strftime('%Y-%m-%d %H:%M:%S')}",
                    "",
                ]
                # Traitement SÉQUENTIEL : une action à la fois.
                for jt, ref in actions:
                    jid = jobs.enqueue_job(ref, jt)
                    if not jid:
                        report.append(f"[SKIP ] {jt} {ref} (déjà actif)")
                        log_to_file("TESTER", f"skip {jt} {ref}")
                        continue
                    report.append(f"[QUEUE] {jt} {ref} -> job #{jid}")
                    log_to_file("TESTER", f"queued {jt} {ref} -> {jid}")
                    waited = 0
                    final = None
                    while waited < 900:
                        time.sleep(3)
                        waited += 3
                        st, _ = jobs.job_status(jid)
                        if st in (None, "installed", "removed", "failed", "cancelled"):
                            final = st
                            break
                    report.append(f"[DONE ] {jt} {ref} -> {final}")
                    log_to_file("TESTER", f"done {jt} {ref} -> {final}")
                try:
                    done = script.with_name(script.stem + ".done" + script.suffix)
                    script.rename(done)
                    report.append(f"\nscript archivé: {done}")
                except Exception as e:
                    report.append(f"\n(archivage échoué: {e})")
                report_path = tests_dir / "test-report.txt"
                report_path.write_text("\n".join(report) + "\n")
                log_to_file("TESTER", f"report written: {report_path}")
        except Exception as e:
            log_to_file("TESTER", f"error: {e}")
        time.sleep(3)


if __name__ == "__main__":
    run_tester_service()
