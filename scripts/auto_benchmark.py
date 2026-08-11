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


def stop_team_agents(team_prefix: str) -> None:
    """ARRÊTE tous les agents de la team (kill → déshydratation)."""
    from modules.sql.agents_repo import AgentsDB
    db = AgentsDB()
    try:
        rows = db.conn.execute(
            "SELECT agent_id FROM agents WHERE name LIKE ?", (team_prefix,)).fetchall()
        ids = [r["agent_id"] for r in rows]
    finally:
        db.close()
    try:
        from services.agent_manager.service import AgentManager
        mgr = AgentManager()
        for aid in ids:
            try:
                mgr.kill(aid)
            except Exception:
                pass
    except Exception as e:
        log(f"  stop agents ERREUR: {e}")
    log(f"  {len(ids)} agents arrêtés")


def recreate_team(team: str) -> None:
    """Supprime et RECRÉE les agents de la team (homes propres).

    Supprimer un agent BDD + son home, puis le re-booter via TeamManager →
    entrée propre (variables, config, home). C'est la bonne façon de « reset » :
    vider juste les fichiers laissait un agent RUNNING sans log."""
    from modules.sql.agents_repo import AgentsDB
    import shutil
    db = AgentsDB()
    try:
        rows = db.conn.execute(
            "SELECT agent_id, name FROM agents WHERE name LIKE ?",
            (f"team:{team}%",)).fetchall()
        for r in rows:
            home = Path.home() / ".modelweaver" / "agent_home" / str(r["agent_id"])
            shutil.rmtree(home, ignore_errors=True)
            db.conn.execute("DELETE FROM agents WHERE agent_id = ?", (r["agent_id"],))
        db.conn.commit()
        log(f"  {len(rows)} agents supprimés (BDD + homes)")
    finally:
        db.close()
    # re-boot la team (recrée les agents avec homes propres)
    try:
        from services.team_manager import TeamManager
        mgr = TeamManager()
        mgr.register(f"services/manifests/teams/{team}.team.yaml")
        log(f"  team {team} recréée")
    except Exception as e:
        log(f"  recreate team ERREUR: {e}")


def recovery(team: str, reason: str) -> None:
    log(f"RECOVERY ({reason}) :")
    team_prefix = f"team:{team}%"
    # 1. ARRÊTER les agents AVANT tout (jamais de reset d'un agent actif)
    stop_team_agents(team_prefix)
    # 2. pause la team (le waker ne les réveille pas pendant la remise en état)
    pause_team(team)
    # 3. purge runtime orphelins + wait_for
    from modules.sql.agents_repo import AgentsDB
    db = AgentsDB()
    try:
        db.conn.execute("DELETE FROM agent_runtime")
        db.conn.commit()
        log("  agent_runtime purgé")
    finally:
        db.close()
    purge_wait_for(team_prefix)
    # 4. supprime + recrée les agents (homes propres)
    recreate_team(team)
    # 5. restart services pour repartir propre
    restart_service("agent-manager")
    restart_service("api")
    time.sleep(3)
    # 6. dé-pause (les agents recréés peuvent travailler)
    unpause_team(team)


# ── Boucle principale ────────────────────────────────────────────────────

def create_task() -> int:
    """Crée une tâche de benchmark, retourne son id."""
    r = subprocess.run([sys.executable, str(BENCH), "--create"],
                       capture_output=True, text=True, timeout=60)
    out = r.stdout or ""
    for line in out.splitlines():
        if "created task=" in line:
            return int(line.split("task=")[1].strip())
    log(f"  create_task échec: {out[-200:]}")
    return -1


def check_task(task_id: int) -> str:
    """Statut d'une tâche : 'todo' | 'doing' | 'done' | 'absent'."""
    r = subprocess.run([sys.executable, str(BENCH), "--status", str(task_id)],
                       capture_output=True, text=True, timeout=60)
    out = r.stdout or ""
    for line in out.splitlines():
        if "status=" in line:
            return line.split("status=")[1].strip()
    return "absent"


def supervise_cycle(team: str, interval_s: int, stagnant_cycles: int) -> None:
    """Crée UNE tâche, puis supervise toutes les `interval_s` secondes.

    Ne relance PAS de benchmark : on laisse le swarm terminer. Recovery si la
    tâche stagne (même statut pendant `stagnant_cycles` contrôles)."""
    log("  création tâche…")
    tid = create_task()
    if tid < 0:
        recovery(team, "création tâche échouée")
        return
    log(f"  tâche {tid} créée — supervision toutes les {interval_s}s")
    last_status = "todo"
    last_change = time.monotonic()
    stale = 0
    try:
        while True:
            time.sleep(interval_s)
            st = check_task(tid)
            log(f"  supervise #{tid}: {st}")
            if st == "done":
                log(f"  ✓ tâche {tid} TERMINÉE (done) — swarm a livré")
                return
            if st == "absent":
                recovery(team, f"tâche {tid} disparue")
                return
            if st != last_status:
                last_status = st
                last_change = time.monotonic()
                stale = 0
                continue
            # STAGNATION : seulement si la tâche est bloquée en 'doing' (le
            # todo → doing est rapide, ~5s). Un 'doing' qui ne bouge pas =
            # greedy coincé sur la finalisation → recovery.
            if st == "doing":
                stale += 1
                if stale >= stagnant_cycles:
                    log(f"  ✗ tâche {tid} bloquée en doing depuis "
                        f"{stagnant_cycles}x{interval_s}s — recovery")
                    recovery(team, f"stagnation {st}")
                    return
    except KeyboardInterrupt:
        raise


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cycles", type=int, default=0, help="0 = infini")
    p.add_argument("--interval", type=int, default=300,
                   help="secondes entre chaque contrôle (5 min)")
    p.add_argument("--stagnant-cycles", type=int, default=3,
                   help="nb de contrôles identiques avant recovery")
    p.add_argument("--team", default="llm-code")
    args = p.parse_args()

    log(f"auto_benchmark start — team={args.team} cycles={args.cycles or '∞'} "
        f"interval={args.interval}s stagnant={args.stagnant_cycles}")
    cycle = 0
    try:
        while args.cycles == 0 or cycle < args.cycles:
            cycle += 1
            log(f"── cycle {cycle} ──")
            supervise_cycle(args.team, args.interval, args.stagnant_cycles)
            time.sleep(args.interval)   # respiration entre cycles
    except KeyboardInterrupt:
        log("auto_benchmark arrêté (Ctrl-C)")
    log("auto_benchmark terminé")


if __name__ == "__main__":
    main()
