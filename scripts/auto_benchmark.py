#!/usr/bin/env python3
"""auto_benchmark — Boucle autonome de debug du swarm (fenêtre longue).

Lance le benchmark réel (bench_swarm_live) à répétition. À chaque cycle :
  1. Lance le benchmark (timeout).
  2. DIAGNOSTIQUE : a-t-il avancé (pick/done) ? bloqué (agents figés) ?
     pas lancé (daemon/agent-manager down) ?
  3. Si problème → RECOVERY : pause team, restart services, reset agents+homes,
     purge wait_for, recommence.
  4. Journalise tout (auto_benchmark.log) pour relecture.

Usage :
    python3 scripts/auto_benchmark.py [--cycles N] [--timeout 300]
                                     [--sleep-between 30] [--team llm-code]

S'arrête à --cycles (défaut infini) ; Ctrl-C propre.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

LOG_PATH = Path(os.environ.get("MODELWEAVER_HOME")
                or Path.home() / ".modelweaver") / "logs" / "auto_benchmark.log"

BENCH = REPO / "benchmarks" / "bench_swarm_live.py"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# ── Diagnostics ──────────────────────────────────────────────────────────

def agents_running() -> int:
    try:
        from modules.sql.agents_repo import AgentsDB
        db = AgentsDB()
        n = db.conn.execute("SELECT COUNT(*) FROM agent_runtime").fetchone()[0]
        db.close()
        return n
    except Exception:
        return 0


def service_status(name: str) -> str:
    """'running' | 'down' pour un service superviseur."""
    try:
        from services.supervisor.client import get_supervisor_client
        c = get_supervisor_client()
        if not c.ping():
            return "down"
        s = c.status().get("services", {}).get(name, {})
        return s.get("status", "down")
    except Exception:
        return "down"


def last_log_activity(age_s: int = 60) -> int:
    """Nb de logs FSM modifiés dans les `age_s` dernières secondes."""
    import glob
    recent = 0
    for f in glob.glob(str(Path.home() / ".modelweaver" / "agent_home"
                            / "*" / "log" / "fsm_*.log")):
        try:
            if time.time() - os.path.getmtime(f) < age_s:
                recent += 1
        except OSError:
            pass
    return recent


def diagnose(report: dict) -> str:
    """'avance' | 'bloque' | 'pas_lance' | 'ok' selon le rapport + l'état."""
    api = service_status("api")
    am = service_status("agent-manager")
    if api != "running" or am != "running":
        return "pas_lance"
    if report.get("status") in ("ok", "ok_pick"):
        return "ok"
    # timeout : regarder si le swarm est bloqué (agents figés) ou juste lent
    if last_log_activity(age_s=30) == 0 and agents_running() == 0:
        return "bloque"   # rien ne tourne, rien ne bouge
    return "lent"         # il y a de l'activité mais le benchmark n'a pas abouti


# ── Recovery ─────────────────────────────────────────────────────────────

def pause_team(team: str) -> None:
    from services.api.handlers.pause import _set_team_pause
    r = _set_team_pause(team, True)
    log(f"  pause team {team}: {r.get('status')}")


def unpause_team(team: str) -> None:
    from services.api.handlers.pause import _set_team_pause
    r = _set_team_pause(team, False)
    log(f"  unpause team {team}: {r.get('status')}")


def restart_service(name: str) -> None:
    try:
        from services.supervisor.client import get_supervisor_client
        c = get_supervisor_client()
        r = c.restart(name)
        log(f"  restart {name}: {r.get('status')}")
    except Exception as e:
        log(f"  restart {name} ERREUR: {e}")


def purge_wait_for(team_prefix: str) -> None:
    from modules.sql.agents_repo import AgentsDB
    db = AgentsDB()
    try:
        n = db.conn.execute(
            f"UPDATE wait_for SET status='waiting', ready_at=NULL "
            f"WHERE agent_id IN (SELECT agent_id FROM agents "
            f"WHERE name LIKE ?)", (team_prefix,)).rowcount
        db.conn.commit()
        log(f"  {n} wait_for remis en waiting")
    finally:
        db.close()


def reset_agents_homes(team_prefix: str) -> None:
    """Reset les homes des agents de la team (vide agent_home/{id})."""
    from modules.sql.agents_repo import AgentsDB
    db = AgentsDB()
    try:
        rows = db.conn.execute(
            "SELECT agent_id FROM agents WHERE name LIKE ?", (team_prefix,)).fetchall()
        for r in rows:
            home = Path.home() / ".modelweaver" / "agent_home" / str(r["agent_id"])
            if home.is_dir():
                import shutil
                shutil.rmtree(home, ignore_errors=True)
        log(f"  homes reset ({len(rows)} agents)")
    finally:
        db.close()


def recovery(team: str, reason: str) -> None:
    log(f"RECOVERY ({reason}) :")
    team_prefix = f"team:{team}%"
    pause_team(team)
    # kill les threads résiduels via restart
    restart_service("agent-manager")
    time.sleep(2)
    # purge agent_runtime orphelins + wait_for
    from modules.sql.agents_repo import AgentsDB
    db = AgentsDB()
    try:
        db.conn.execute("DELETE FROM agent_runtime")
        db.conn.commit()
        log("  agent_runtime purgé")
    finally:
        db.close()
    purge_wait_for(team_prefix)
    reset_agents_homes(team_prefix)
    # restart api aussi (le pilote dev-chat y vit)
    restart_service("api")
    time.sleep(3)
    unpause_team(team)


# ── Boucle principale ────────────────────────────────────────────────────

def run_bench(timeout_s: int) -> dict:
    """Lance bench_swarm_live, retourne son rapport."""
    try:
        r = subprocess.run(
            [sys.executable, str(BENCH), "--timeout", str(timeout_s)],
            capture_output=True, text=True, timeout=timeout_s + 20)
        out = r.stdout or ""
        log(f"  bench stdout: {out.strip()[-200:]}")
        if r.returncode == 0:
            return {"status": "ok"}
        if "ok_pick" in out:
            return {"status": "ok_pick"}
        if "timeout" in out:
            return {"status": "timeout"}
        return {"status": "error", "detail": out[-300:]}
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cycles", type=int, default=0, help="0 = infini")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--sleep-between", type=int, default=30)
    p.add_argument("--team", default="llm-code")
    args = p.parse_args()

    log(f"auto_benchmark start — team={args.team} cycles={args.cycles or '∞'} "
        f"timeout={args.timeout}s")
    cycle = 0
    try:
        while args.cycles == 0 or cycle < args.cycles:
            cycle += 1
            log(f"── cycle {cycle} ──")
            report = run_bench(args.timeout)
            diag = diagnose(report)
            log(f"  diagnostic: {diag} | report={report.get('status')}")

            if diag == "ok":
                log("  ✓ swarm fonctionne (pick/done)")
            elif diag in ("bloque", "pas_lance"):
                recovery(args.team, diag)
            else:  # lent
                log("  ~ swarm lent (activité mais pas d'aboutissement)")

            time.sleep(args.sleep_between)
    except KeyboardInterrupt:
        log("auto_benchmark arrêté (Ctrl-C)")
    log("auto_benchmark terminé")


if __name__ == "__main__":
    main()
