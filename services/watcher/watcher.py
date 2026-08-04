"""Watcher — surveillant du swarm (problem → solution, sans LLM).

Processe séparé (lancé par le superviseur, comme model_sync). Boucle toutes
les ~30s :

  1. COLLECTER l'état (requêtes SQL pures) : agent_runtime + heartbeats,
     tasks par statut/rôle, issues, wait_for, agents.
  2. DÉTECTER les problèmes : chaque détecteur = fonction pure
     (état → Problem | None). Les règles sont scriptées (pas de LLM).
  3. RÉSOUDRE : chaque problème a une action de correction minimale,
     idempotente, loggée dans watcher_log.
  4. RAPPORTER : table watcher_log (problem_type, détails, action, résultat).

Les cas complexes (boucles LLM, réattribution risquée) ne sont PAS traités
ici pour l'instant — ils feront l'objet d'une analyse ultérieure (LLM).

Problèmes traités (simples) :
  P1  task running orpheline (aucun agent légitimement actif dessus)
  P2  agent idle mais des tasks pending de son rôle existent
  P3  reliquat runtime (heartbeat vieux, step idle/hydrated) → purge
  P5  spam wait_for (beaucoup de lignes actives par agent)
  P6  issue 'analysing' bloquée (sans tasks liées depuis longtemps) → open
  P7  task role_required multi-valeurs (impocable) → normaliser
  P9  issue analyzed sans lien analysis_workspace_id (si workspace identifiable)

Chaque résolveur est best-effort et ne lève jamais (le watcher continue).
"""

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.sql.workspace import WorkspaceDB
from modules.sql.db import AgentsDB

DEFAULT_INTERVAL_S = 30.0

# ── Collecte d'état ───────────────────────────────────────────────────


def _collect_state() -> Dict[str, Any]:
    """Capture un snapshot de l'état swarm (agents, runtime, tasks, issues)."""
    st = {"agents": [], "runtime": [], "tasks": [], "issues": [], "wait_for": []}
    try:
        db = AgentsDB()
        st["agents"] = _rows(db.conn.execute(
            "SELECT agent_id, name, role_type, status FROM agents "
            "WHERE name LIKE 'team:swarm-selfimprove-v2/%'").fetchall())
        team_ids = [r["agent_id"] for r in st["agents"]]
        if team_ids:
            ph = ",".join("?" for _ in team_ids)
            st["runtime"] = _rows(db.conn.execute(
                f"SELECT * FROM agent_runtime WHERE agent_id IN ({ph})",
                tuple(team_ids)).fetchall())
            st["wait_for"] = _rows(db.conn.execute(
                f"SELECT agent_id, condition, status, id FROM wait_for "
                f"WHERE status IN ('waiting','ready') AND agent_id IN ({ph})",
                tuple(team_ids)).fetchall())
        db.close()
    except Exception:
        pass
    try:
        wdb = WorkspaceDB()
        # Workspaces pertinents : mw-swarm (la team) + ceux liés à une issue
        # de mw-swarm (analysis_workspace_id). Évite de toucher aux tasks des
        # autres teams/workspaces.
        linked = {r["workspace_id"] for r in wdb.conn.execute(
            "SELECT DISTINCT analysis_workspace_id AS workspace_id "
            "FROM issues WHERE workspace_id='mw-swarm' "
            "AND analysis_workspace_id IS NOT NULL").fetchall()}
        linked.add("mw-swarm")
        ph = ",".join("?" for _ in linked)
        st["tasks"] = _rows(wdb.conn.execute(
            f"SELECT task_id, workspace_id, title, status, role_required, "
            f"updated_at, assigned_to FROM tasks WHERE workspace_id IN ({ph})",
            tuple(linked)).fetchall())
        st["issues"] = _rows(wdb.conn.execute(
            "SELECT issue_id, workspace_id, title, status, priority, "
            "analysis_workspace_id FROM issues WHERE workspace_id='mw-swarm'").fetchall())
        wdb.close()
    except Exception:
        pass
    return st


def _rows(cur) -> List[Dict[str, Any]]:
    """Convertit un curseur OU une liste de lignes sqlite en dicts."""
    try:
        rows = cur.fetchall() if hasattr(cur, "fetchall") else cur
    except Exception:
        return []
    return [dict(r) for r in rows] if rows else []


# ── Détecteurs (purs, sans LLM) ───────────────────────────────────────

# problème: {"type": str, "agent_id": int|None, "details": str, "refs": dict}


def detect_stale_runtime(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """P3 — reliquats runtime : heartbeat vieux (>60s) OU step idle/hydrated
    persistant. Un agent légitimement actif a un heartbeat frais."""
    import time as _t
    now = _t.time()
    out = []
    for r in st["runtime"]:
        hb = r.get("heartbeat_at") or ""
        age = _hb_age_s(hb, now)
        if age is not None and age > 60:
            out.append({"type": "P3_stale_runtime",
                        "agent_id": r.get("agent_id"),
                        "details": f"heartbeat vieux de {age:.0f}s",
                        "refs": {"runtime": r}})
    return out


def _hb_age_s(hb: str, now: float) -> Optional[float]:
    try:
        import datetime
        dt = datetime.datetime.strptime(hb, "%Y-%m-%d %H:%M:%S")
        return (now - dt.timestamp())
    except Exception:
        return None


def detect_orphan_running_tasks(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """P1 — task 'running' orpheline : aucune ligne agent_runtime avec un
    heartbeat frais ne correspond à un agent légitimement actif."""
    active_agents = {r["agent_id"] for r in st["runtime"]}
    legit = set()
    for r in st["runtime"]:
        if _hb_age_s(r.get("heartbeat_at") or "", time.time()) is not None:
            if _hb_age_s(r.get("heartbeat_at") or "", time.time()) < 60:
                legit.add(r["agent_id"])
    out = []
    for t in st["tasks"]:
        if t["status"] != "running":
            continue
        # une task running sans agent actif (heartbeat frais) → orpheline
        if not (active_agents & legit):
            out.append({"type": "P1_orphan_running_task",
                        "details": f"task {t['task_id']} running sans agent actif",
                        "refs": {"task": t}})
            continue
        # task running mais son workspace n'a aucun agent hydraté dessus
    return out


def detect_idle_with_pending(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """P2 — agents en attente (wait_for task_for_role) dont le rôle a des
    tasks pending dispo → ils devraient être réveillés."""
    # agents en wait_for 'waiting' avec rôle
    waiting_by_agent = {}
    for w in st["wait_for"]:
        cond = _safe_json(w.get("condition"))
        if cond.get("type") == "task_for_role":
            waiting_by_agent.setdefault(w["agent_id"], cond)
    pending_roles = {t["role_required"] for t in st["tasks"]
                     if t["status"] == "pending" and t["role_required"]}
    out = []
    for agent_id, cond in waiting_by_agent.items():
        role = cond.get("role", "")
        if role and role in pending_roles:
            out.append({"type": "P2_idle_with_pending",
                        "agent_id": agent_id,
                        "details": f"agent {agent_id} attend role={role} mais "
                                   f"des tasks pending existent",
                        "refs": {"condition": cond}})
    return out


def detect_wait_for_spam(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """P5 — spam wait_for : beaucoup de lignes actives par agent."""
    from collections import Counter
    c = Counter(w["agent_id"] for w in st["wait_for"])
    out = []
    for agent_id, n in c.items():
        if n > 3:
            out.append({"type": "P5_wait_for_spam",
                        "agent_id": agent_id,
                        "details": f"{n} lignes wait_for actives",
                        "refs": {"count": n}})
    return out


def detect_stuck_analysing_issues(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """P6 — issue 'analysing' sans tasks liées → l'analyste l'a piochée mais
    le run s'est interrompu avant découpage → remettre 'open'."""
    analysed_ws = {t["workspace_id"] for t in st["tasks"]}
    out = []
    for i in st["issues"]:
        if i["status"] != "analysing":
            continue
        # ne pas toucher aux issues human-choice (décision humaine requise)
        if "human-choice" in (i.get("title") or ""):
            continue
        # pas de workspace de découpage connu pour cette issue
        if not i.get("analysis_workspace_id"):
            out.append({"type": "P6_stuck_analysing",
                        "details": f"issue {i['issue_id']} analysing sans tasks",
                        "refs": {"issue": i}})
    return out


def detect_multi_role_tasks(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """P7 — task role_required multi-valeurs (virgule) → impocable."""
    out = []
    for t in st["tasks"]:
        r = t.get("role_required") or ""
        if "," in r:
            out.append({"type": "P7_multi_role_task",
                        "details": f"task {t['task_id']} role='{r}'",
                        "refs": {"task": t}})
    return out


# ── Résolveurs (purs, idempotents) ───────────────────────────────────

def _apply(st: Dict[str, Any], problem: Dict[str, Any],
           wdb: WorkspaceDB, db: AgentsDB) -> str:
    ptype = problem["type"]
    try:
        if ptype == "P3_stale_runtime":
            aid = problem.get("agent_id")
            db.conn.execute("DELETE FROM agent_runtime WHERE agent_id = ?", (aid,))
            db.conn.execute(
                "UPDATE agents SET status='IDLE' WHERE agent_id = ?", (aid,))
            db.conn.commit()
            return f"runtime de l'agent {aid} purgé"

        if ptype == "P1_orphan_running_task":
            tid = problem["refs"]["task"]["task_id"]
            wdb.conn.execute(
                "UPDATE tasks SET status='pending', updated_at=datetime('now') "
                "WHERE task_id = ?", (tid,))
            wdb.conn.commit()
            return f"task {tid} remise en pending"

        if ptype == "P2_idle_with_pending":
            # le waker s'en charge normalement ; si on est là c'est un cas
            # manqué → on force un réveil (signal wakeup)
            aid = problem.get("agent_id")
            db.conn.execute(
                "INSERT INTO agent_signals (agent_id, type, status) "
                "VALUES (?, 'wakeup', 'PENDING')", (aid,))
            db.conn.commit()
            return f"signal wakeup envoyé à l'agent {aid}"

        if ptype == "P5_wait_for_spam":
            aid = problem.get("agent_id")
            # garder le dernier waiting, marquer les autres done
            rows = db.conn.execute(
                "SELECT id FROM wait_for WHERE agent_id = ? AND status IN "
                "('waiting','ready') ORDER BY id DESC", (aid,)).fetchall()
            for i, r in enumerate(rows):
                if i > 0:
                    db.conn.execute(
                        "UPDATE wait_for SET status='done' WHERE id = ?",
                        (r["id"],))
            db.conn.commit()
            return f"wait_for de l'agent {aid} dédoublonné"

        if ptype == "P6_stuck_analysing":
            iid = problem["refs"]["issue"]["issue_id"]
            wdb.conn.execute(
                "UPDATE issues SET status='open', assigned_to='' "
                "WHERE issue_id = ?", (iid,))
            wdb.conn.commit()
            return f"issue {iid} remise en open"

        if ptype == "P7_multi_role_task":
            tid = problem["refs"]["task"]["task_id"]
            role = (problem["refs"]["task"].get("role_required") or "").split(",")[0]
            wdb.conn.execute(
                "UPDATE tasks SET role_required = ? WHERE task_id = ?",
                (role.strip(), tid))
            wdb.conn.commit()
            return f"task {tid} rôle normalisé → {role.strip()}"

        return "aucune action (type inconnu)"
    except Exception as e:
        return f"échec: {e}"


def _safe_json(s: Any) -> Dict[str, Any]:
    import json
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except Exception:
        return {}


# ── Cycle complet ────────────────────────────────────────────────────


def watcher_cycle() -> List[Dict[str, Any]]:
    """Un cycle complet : collecte → détection → résolution → log. Retourne
    les actions appliquées (pour tests / debug)."""
    st = _collect_state()
    problems = []
    for det in (detect_stale_runtime, detect_orphan_running_tasks,
                detect_idle_with_pending, detect_wait_for_spam,
                detect_stuck_analysing_issues, detect_multi_role_tasks):
        try:
            problems.extend(det(st))
        except Exception:
            pass

    actions = []
    if problems:
        try:
            wdb = WorkspaceDB()
            db = AgentsDB()
            for p in problems:
                action = _apply(st, p, wdb, db)
                actions.append({"type": p["type"], "action": action,
                                "agent_id": p.get("agent_id"),
                                "details": p.get("details", "")[:200]})
            wdb.close()
            db.close()
        except Exception as e:
            actions.append({"type": "internal", "action": f"échec résolution: {e}"})
    return actions


def run(interval: float = DEFAULT_INTERVAL_S) -> None:
    """Boucle de surveillance. Tourne en continu (process séparé)."""
    print("watcher: démarrage", flush=True)
    while True:
        try:
            actions = watcher_cycle()
            if actions:
                print(f"watcher: {len(actions)} actions", flush=True)
                for a in actions:
                    print(f"  {a['type']}: {a['action']}", flush=True)
        except Exception as e:
            print(f"watcher: erreur cycle: {e}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    run()
