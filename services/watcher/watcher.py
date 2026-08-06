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
            "WHERE name LIKE 'team:%/%'").fetchall())
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
        # Workspaces des teams actives : tous les workspaces dirigés par une
        # team (director LIKE 'team:%') + les workspaces liés aux issues de
        # mw-swarm (rétro-compatibilité). Générique : permet à CHAQUE team
        # (ex. gui-tasks → mw-gui-tasks) de recevoir ses issues/tasks.
        team_ws = {r["workspace_id"] for r in wdb.conn.execute(
            "SELECT DISTINCT workspace_id FROM workspaces "
            "WHERE director LIKE 'team:%'").fetchall()}
        # autres workspaces liés à une issue de mw-swarm (ancien comportement)
        linked = {r["workspace_id"] for r in wdb.conn.execute(
            "SELECT DISTINCT analysis_workspace_id AS workspace_id "
            "FROM issues WHERE workspace_id='mw-swarm' "
            "AND analysis_workspace_id IS NOT NULL").fetchall()}
        linked.add("mw-swarm")
        watched = team_ws | linked
        ph = ",".join("?" for _ in watched)
        st["tasks"] = _rows(wdb.conn.execute(
            f"SELECT task_id, workspace_id, title, status, role_required, "
            f"updated_at, assigned_to FROM tasks WHERE workspace_id IN ({ph})",
            tuple(watched)).fetchall())
        st["issues"] = _rows(wdb.conn.execute(
            f"SELECT issue_id, workspace_id, title, status, priority, "
            f"analysis_workspace_id FROM issues WHERE workspace_id IN ({ph})",
            tuple(watched)).fetchall())
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


# Seuils P8 (agent qui travaille mais semble bloqué / LLM mort ou très lent)
P8_MAX_LOG_SILENCE_S = 300      # pas de nouvelle ligne FSM depuis 5 min
P8_MAX_LLM_LATENCY_S = 120      # llm/call → llm/ok > 2 min → LLM très lent
P8_CONSEC_ERRORS = 6            # ≥6 llm/error consécutifs sans tool ok → boucle


def _fsm_log_path(agent_id: int) -> Optional[Path]:
    """Dernier log FSM de l'agent (le plus récent par mtime)."""
    try:
        from services._common import mw_home
        log_dir = Path(mw_home()) / "agent_home" / str(agent_id) / "log"
        if not log_dir.is_dir():
            return None
        files = sorted(log_dir.glob("fsm_*.log"), key=lambda p: p.stat().st_mtime)
        return files[-1] if files else None
    except Exception:
        return None


def _fsm_activity(agent_id: int) -> Dict[str, Any]:
    """Analyse le FSM log récent d'un agent.

    Retourne : {mtime_age_s, last_round, llm_latencies, consec_errors,
    errors, total_lines, has_recent_tool_ok}. Ne lève jamais."""
    import re
    out = {"mtime_age_s": None, "last_round": None, "max_llm_latency_s": 0.0,
           "consec_errors": 0, "errors": 0, "total_lines": 0,
           "has_recent_tool_ok": False, "error_types": []}
    path = _fsm_log_path(agent_id)
    if not path:
        return out
    try:
        out["mtime_age_s"] = time.time() - path.stat().st_mtime
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return out
    out["total_lines"] = len(lines)
    # erreurs consécutives SANS tool/ok (boucle d'erreur LLM)
    consec = 0
    err_types = []
    for line in lines:
        if "llm/error" in line:
            consec += 1
            m = re.search(r"err=\[?([a-z_]+)", line)
            if m and m.group(1) not in err_types:
                err_types.append(m.group(1))
            out["errors"] += 1
        elif "tool/ok" in line:
            consec = 0
        else:
            consec = max(0, consec)
    out["consec_errors"] = consec
    out["error_types"] = err_types
    # round max + latence llm/call → llm/ok
    last_call_ts = None
    max_lat = 0.0
    for line in lines:
        if "llm/call" in line:
            m = re.match(r"(\d\d):(\d\d):(\d\d)\.\d+", line)
            if m:
                h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
                last_call_ts = h * 3600 + mi * 60 + s
                rm = re.search(r"round=(\d+)", line)
                if rm:
                    out["last_round"] = int(rm.group(1))
        elif "llm/ok" in line and last_call_ts is not None:
            m = re.match(r"(\d\d):(\d\d):(\d\d)\.\d+", line)
            if m:
                h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
                lat = (h * 3600 + mi * 60 + s) - last_call_ts
                if lat < 0:
                    lat += 24 * 3600  # minuit
                max_lat = max(max_lat, lat)
            last_call_ts = None
    out["max_llm_latency_s"] = max_lat
    # un tool/ok récent (dans les ~60 dernières lignes) → l'agent agit
    out["has_recent_tool_ok"] = any("tool/ok" in l for l in lines[-60:])
    return out


def detect_unlinked_issue_workspace(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """P9 — issue analyzed/analysing sans analysis_workspace_id, avec un
    workspace identifiable par son contenu (mapping manuel).

    Permet au marquage 'done' de fonctionner : une issue sans lien ne passera
    jamais 'done' même quand ses tasks sont terminées. Ne concerne QUE les
    issues non human-choice (les autres attendent une décision humaine).
    """
    # Mapping issue_id → workspace, basé sur le contenu des tasks observé.
    # Nouvelles issues → le workflow de l'analyste crée workspace-issue-<id>
    # et le lien est fait via issue_link_workspace (pas besoin ici).
    MAP = {
        2: "audit-io-2024",
        9: "sec_keyring_001",
        12: "llm_cb_001",
        17: "direct-bridge-tests",
        27: "debug-removal-recipe-parser",
    }
    out = []
    for i in st["issues"]:
        if i.get("analysis_workspace_id"):
            continue
        if i["status"] not in ("analyzed", "analysing"):
            continue
        if "human-choice" in (i.get("title") or ""):
            continue
        ws = MAP.get(i["issue_id"])
        if not ws:
            continue
        out.append({"type": "P9_unlinked_issue",
                    "details": f"issue {i['issue_id']} → workspace {ws}",
                    "refs": {"issue": i, "workspace": ws}})
    return out


def detect_unblocked_issues(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """P10 — issue 'blocked' dont un human_choice est 'answered' → la décision
    humaine est arrivée, l'issue doit être débloquée (open + réponse injectée).
    """
    out = []
    try:
        from modules.sql.workspace import WorkspaceDB
        wdb = WorkspaceDB()
        rows = wdb.conn.execute(
            "SELECT h.choice_id, h.issue_id, h.response, i.title "
            "FROM human_choice h JOIN issues i ON i.issue_id = h.issue_id "
            "WHERE h.status = 'answered' AND i.status = 'blocked'").fetchall()
        wdb.close()
        for r in rows:
            out.append({"type": "P10_unblock_issue",
                        "details": f"issue {r['issue_id']} débloquée (choix {r['choice_id']})",
                        "refs": {"issue_id": r["issue_id"],
                                 "response": r["response"] or "",
                                 "choice_id": r["choice_id"]}})
    except Exception:
        pass
    return out


def detect_stalled_agent(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """P8 — agent actif mais qui semble bloqué (FSM).

    Cas détectés :
      - silence FSM : aucune nouvelle ligne depuis P8_MAX_LOG_SILENCE_S
      - LLM très lent : latence llm/call→llm/ok > P8_MAX_LLM_LATENCY_S
      - boucle d'erreurs : ≥ P8_CONSEC_ERRORS llm/error consécutifs sans tool ok
    """
    out = []
    """P8 — agent actif mais qui semble bloqué (FSM).

    Cas détectés :
      - silence FSM : aucune nouvelle ligne depuis P8_MAX_LOG_SILENCE_S
      - LLM très lent : latence llm/call→llm/ok > P8_MAX_LLM_LATENCY_S
      - boucle d'erreurs : ≥ P8_CONSEC_ERRORS llm/error consécutifs sans tool ok
    """
    out = []
    for r in st["runtime"]:
        aid = r.get("agent_id")
        act = _fsm_activity(aid)
        if act["mtime_age_s"] is None:
            continue
        problems = []
        if act["mtime_age_s"] > P8_MAX_LOG_SILENCE_S:
            problems.append(f"FSM silencieux depuis {act['mtime_age_s']:.0f}s")
        if act["max_llm_latency_s"] > P8_MAX_LLM_LATENCY_S:
            problems.append(f"latence LLM max {act['max_llm_latency_s']:.0f}s")
        if act["consec_errors"] >= P8_CONSEC_ERRORS:
            problems.append(f"{act['consec_errors']} erreurs LLM consécutives "
                            f"({','.join(act['error_types'])})")
        if not problems:
            continue
        out.append({"type": "P8_stalled_agent",
                    "agent_id": aid,
                    "details": "; ".join(problems),
                    "refs": {"activity": act}})
    return out


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
    """P2 — agents de la team dont le rôle a des tasks pending dispo mais qui
    ne sont pas actifs (pas hydratés). Ils devraient être réveillés.

    Couvre AUSSI les agents sans wait_for (jamais réveillés — ex. les testers
    bloqués par des reliquats runtime INIT), pas seulement ceux en attente."""
    # rôle de task attendu par chaque rôle d'agent (même mapping que le waker)
    ROLE_TO_TASK = {
        "architecte": "analyst", "planificateur": "analyst",
        "codeur": "coder_senior", "test_runner": "tester",
        "relecteur": "reviewer", "orchestrateur": "merger",
    }
    pending_roles = {t["role_required"] for t in st["tasks"]
                     if t["status"] == "pending" and t["role_required"]}
    active_ids = {r["agent_id"] for r in st["runtime"]}
    out = []
    for a in st["agents"]:
        aid = a.get("agent_id")
        # agent déjà hydraté → pas besoin de le réveiller
        if aid in active_ids:
            continue
        rt = a.get("role_type", "")
        task_role = ROLE_TO_TASK.get(rt, "")
        if not task_role or task_role not in pending_roles:
            continue
        # l'analyste (analyst) est géré par les issues ; merger par all_done.
        if task_role in ("analyst", "merger"):
            continue
        out.append({"type": "P2_idle_with_pending",
                    "agent_id": aid,
                    "details": f"agent {aid} ({rt}) non actif mais tasks "
                               f"{task_role} pending dispo",
                    "refs": {"role": task_role}})
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

        if ptype == "P8_stalled_agent":
            # kill l'agent bloqué + libérer sa task running (il est probablement
            # coincé sur une boucle LLM). L'agent pourra être re-réveillé.
            aid = problem.get("agent_id")
            n = 0
            # task running de cet agent ? (assigned_to peut être vide ; on
            # libère les running du workspace par sécurité seulement si l'agent
            # est le seul à tourner sur elles — on se limite à kill + re-wait)
            db.conn.execute("DELETE FROM agent_runtime WHERE agent_id = ?", (aid,))
            db.conn.execute(
                "UPDATE agents SET status='IDLE' WHERE agent_id = ?", (aid,))
            db.conn.commit()
            return f"agent {aid} arrêté (stall FSM), runtime purgé"

        if ptype == "P9_unlinked_issue":
            iid = problem["refs"]["issue"]["issue_id"]
            ws = problem["refs"]["workspace"]
            wdb.conn.execute(
                "UPDATE issues SET analysis_workspace_id = ? WHERE issue_id = ?",
                (ws, iid))
            wdb.conn.commit()
            return f"issue {iid} liée au workspace {ws}"

        if ptype == "P10_unblock_issue":
            iid = problem["refs"]["issue_id"]
            resp = problem["refs"]["response"]
            choice_id = problem["refs"]["choice_id"]
            # injecter la réponse humaine dans l'issue + la débloquer
            r = wdb.conn.execute(
                "SELECT description FROM issues WHERE issue_id = ?", (iid,)).fetchone()
            desc = (r["description"] or "") if r else ""
            note = (chr(10) + chr(10)
                    + f"[DÉCISION HUMAINE ({choice_id})] {resp}")
            wdb.conn.execute(
                "UPDATE issues SET status='open', assigned_to='', "
                "description=?, updated_at=datetime('now') WHERE issue_id = ?",
                (desc + note, iid))
            wdb.conn.commit()
            return f"issue {iid} débloquée, réponse humaine injectée"

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


def _detect_warnings(st: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Warnings (pas des problèmes bloquants) : état du repo central.

    - taille du repo mw-swarm (croissance anormale)
    - fichiers disparus récents dans le working tree des agents (le bug
      git add -A qui supprimait des fichiers)
    - repos par-workspace créés à tort (au lieu de pousser vers mw-swarm)
    """
    out = []
    try:
        from services._common import mw_home
        repos = mw_home() / "repos"
        # 1) taille du repo central mw-swarm
        mw_repo = repos / "mw-swarm.git"
        if mw_repo.exists():
            size_mb = sum(f.stat().st_size for f in mw_repo.rglob("*")
                          if f.is_file()) / (1024 * 1024)
            if size_mb > 200:
                out.append({"type": "W_git_repo_size",
                            "detail": f"mw-swarm.git {size_mb:.0f} Mo "
                                      f"(>200 Mo)"})
        # 2) repos par-workspace créés à tort (le bug project_id)
        try:
            workspace_repos = [p.name for p in repos.glob("*.git")
                               if p.name not in ("mw-swarm.git",)]
            if len(workspace_repos) > 10:
                out.append({"type": "W_many_workspace_repos",
                            "detail": f"{len(workspace_repos)} repos "
                                      f"par-workspace créés (bug project_id ?)"})
        except Exception:
            pass
        # 3) fichiers disparus dans les clones récents (bug git add -A)
        try:
            gone = _detect_disappeared_files()
            if gone:
                out.append({"type": "W_files_disappeared",
                            "detail": f"{len(gone)} fichiers disparus récents "
                                      f"(bug git add -A ?) ex: {gone[0][:80]}"})
        except Exception:
            pass
    except Exception:
        pass
    return out


def _detect_disappeared_files() -> List[str]:
    """Détecte les fichiers qui ont disparu du working tree des agents
    (le bug git add -A qui supprimait des fichiers du framework)."""
    gone = []
    try:
        import subprocess
        from services._common import mw_home
        for clone_dir in (mw_home() / "agent_home").glob("*/workspace/*"):
            if not (clone_dir / ".git").exists():
                continue
            try:
                r = subprocess.run(
                    ["git", "-C", str(clone_dir), "status", "--porcelain"],
                    capture_output=True, text=True, timeout=15)
                for line in (r.stdout or "").splitlines():
                    if line.startswith(" D ") or line.startswith("D "):
                        f = line[3:].strip()
                        if f and "modules/" in f or "services/" in f:
                            gone.append(f"{clone_dir.name}: {f}")
            except Exception:
                continue
    except Exception:
        pass
    return gone[:20]


def watcher_cycle() -> List[Dict[str, Any]]:
    """Un cycle complet : collecte → détection → résolution → log. Retourne
    les actions appliquées (pour tests / debug)."""
    st = _collect_state()
    problems = []
    for det in (detect_stale_runtime, detect_orphan_running_tasks,
                detect_idle_with_pending, detect_wait_for_spam,
                detect_stuck_analysing_issues, detect_multi_role_tasks,
                detect_stalled_agent, detect_unlinked_issue_workspace,
                detect_unblocked_issues):
        try:
            problems.extend(det(st))
        except Exception:
            pass

    warnings = _detect_warnings(st)

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

    # Logging : détaillé (tout le cycle) + synthèse (problèmes récurrents).
    try:
        from services.watcher.watcher_log import cycle_detail, summary
        cycle_detail(_CYCLE[0], st, problems, actions, warnings)
        for a in actions:
            if a.get("action", "").startswith("échec") or "internal" in a["type"]:
                res = "non_resolu"
            else:
                res = "script"
            summary(a["type"], res, a.get("details", ""))
        for w in warnings:
            summary(w["type"], "warning", w.get("detail", ""))
    except Exception:
        pass

    _CYCLE[0] += 1
    return actions


_CYCLE = [0]


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
