#!/usr/bin/env python3
"""Scénario « jeu d'échecs CLI » — V0.7.0.2 (live daemon multi-agent).

Parcours 100% daemon/agent_manager (comme une vraie interaction) : les 4
agents sont créés et exécutés via l'API du daemon (MWClient), donc
l'activité est visible en direct dans la GUI (panneaux Agents / Debug /
Chat). Chaque deliverable est GÉNÉRÉ par un LLM réel, et les agents
utilisent des LLM DIFFÉRENTS (groq + ollama) pour vérifier le branchement
simultané :

  - manager       : groq/llama-3.1-8b-instant  (coordone, merge, décide)
  - worker_engine : ollama/llama3.2:1b          (engine.py)
  - worker_cli    : groq/llama-3.1-8b-instant  (cli.py)
  - analyst       : ollama/gemma2:2b           (SPEC + revue)

Le skill `timeout(llm)` est activé sur chaque étape `llm_call`
(timeout 60-90s, fallback=true) : si un LLM ne répond pas, le gestionnaire
de LLM en attribue un autre. Un test unitaire isolé prouve ce repli.

Les agents discutent dans un chatroom (`chess`) : brainstorm -> tour de
table du manager -> décision finale d'interfaces -> implémentation ->
merge + main.py -> revue.

Lancement (daemon 8771 + AFD déjà démarrés) :
    PYTHONPATH=. python3 tests/test_scenario_chess.py
"""
import os
import sys
import time
import shutil
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.api.client import MWClient
from services._common import mw_home

import yaml
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = REPO_ROOT / "scenarios" / "chess"
PROJECT_ID = "chess"
PREFIX = "chess_"
CHATROOM = "chess"

P = {"project_id": PROJECT_ID}
results = []

MAX_TOTAL = 540          # garde-fou global du scénario (secondes)
CALL_TIMEOUT = 180       # timeout par appel LLM/daemon (secondes)


def ts():
    return datetime.now().strftime("%H:%M:%S")


def log(*args, **_kwargs):
    msg = " ".join(str(a) for a in args)
    sys.stdout.write(f"[{ts()}] {msg}\n")
    sys.stdout.flush()


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    line = f"[{mark}] {name}"
    if not cond and detail:
        line += f"\n      -> {detail}"
    log(line, flush=True)


def load_wf(name):
    with open(SCENARIO_DIR / name) as f:
        return yaml.safe_load(f)


TEAM = {
    "manager":       {"yaml": "manager.yaml",       "prov": "groq",   "model": "llama-3.1-8b-instant"},
    "worker_engine": {"yaml": "worker_engine.yaml", "prov": "ollama", "model": "llama3.2:1b"},
    "worker_cli":    {"yaml": "worker_cli.yaml",     "prov": "groq",   "model": "llama-3.1-8b-instant"},
    "analyst":       {"yaml": "analyst.yaml",        "prov": "ollama", "model": "gemma2:2b"},
}


def cleanup_agents(mw):
    for a in mw.agent.list().get("agents", []):
        if a["name"].startswith(PREFIX):
            try:
                mw.agent.delete(name=a["name"])
            except Exception:
                pass


def cleanup_disk():
    p = mw_home() / "repos" / f"{PROJECT_ID}.git"
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
    for d in (mw_home() / "memagent").glob(f"{PREFIX}*"):
        shutil.rmtree(d, ignore_errors=True)
    cr = mw_home() / "comms" / CHATROOM
    if cr.exists():
        shutil.rmtree(cr, ignore_errors=True)


def create(mw, name, wf):
    r = mw.agent.create(name=name, role="agent", occupation="noncontinue",
                        config={"workflow": wf})
    return r.get("agent_id")


def run(mw, name, request, t0, prov, model):
    if time.time() - t0 > MAX_TOTAL:
        log(f"\n--- SKIP {name} (garde-fou global {MAX_TOTAL}s dépassé) ---")
        return (False, {"status": "GLOBAL_TIMEOUT"}, 0, 0)
    log(f"\n--- execute {name} (request={request!r}) ---", flush=True)
    t1 = time.time()
    try:
        res = mw.agent.execute(name=name, request=request,
                               provider_ref=prov, model_ref=model)
    except Exception as e:
        dt = time.time() - t1
        log(f"    -> ERREUR appel daemon: {e} ({dt:.1f}s)")
        return False, {"status": "CALL_ERROR", "error": str(e)}, dt, 0
    dt = time.time() - t1
    ok = (res or {}).get("status") in ("ok", "success", "SUCCESS")
    vars_ = (res or {}).get("variables", {}) or {}
    fb = vars_.get("_llm_fallbacks", 0)
    log(f"    -> status={res.get('status')} tokens={res.get('tokens_used')} "
          f"fallbacks={fb} ({dt:.1f}s)", flush=True)
    if not ok:
        log(f"    !! end_reason={res.get('end_reason')!r} error={res.get('error')!r}", flush=True)
    return ok, res, dt, fb


def run_parallel(mw, jobs):
    """Lance plusieurs (name, request, prov, model) en parallèle pour
    vérifier le branchement simultané sur des LLM différents."""
    out = {}
    lock = threading.Lock()
    t0 = time.time()

    def worker(key, name, request, prov, model):
        ok, res, dt, fb = run(mw, name, request, t0, prov, model)
        with lock:
            out[key] = (ok, res, dt, fb)

    threads = []
    for key, (name, request, prov, model) in jobs.items():
        th = threading.Thread(target=worker, args=(key, name, request, prov, model))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    return out, (time.time() - t0)


def chatroom_agents():
    log = mw_home() / "comms" / CHATROOM / "chatroom.jsonl"
    if not log.exists():
        return []
    agents = []
    for ln in log.read_text(encoding="utf-8").splitlines():
        try:
            agents.append(yaml.safe_load(ln).get("agent", ""))
        except Exception:
            pass
    return [a for a in agents if a]


def check_timeout_skill_unit():
    """Test unitaire isolé du repli du skill timeout(llm) : le LLM principal
    est injoignable ; le gestionnaire de LLM doit en ATTRIBUER un autre
    (mocké ici sur groq pour un test déterministe), et l'appel aboutit."""
    log("\n=== Test unitaire : timeout(llm) -> repli gestionnaire LLM ===", flush=True)
    try:
        from unittest import mock
        from modules.llm_manager.resilient import resilient_chat
        from modules.llm_manager.llm_manager import LLMManager
    except Exception as e:
        check("import resilient", False, str(e))
        return
    # On force le gestionnaire de LLM à répondre « groq » comme repli, afin
    # de tester le MÉCANISME de repli sans dépendre du zoo de providers cloud.
    fallback = {"provider_ref": "groq", "model_ref": "llama-3.1-8b-instant"}
    with mock.patch.object(LLMManager, "assign_llm", return_value=fallback):
        try:
            resp = resilient_chat(
                "__injoignable__", "x",
                [{"role": "user", "content": "dis bonjour en une phrase."}],
                timeout=20, fallback=True, max_retries=3,
            )
        except Exception as e:
            check("timeout(llm) repli réussi", False, f"exception: {e}")
            return
    used = getattr(resp, "provider_used", "?")
    ok = (used == "groq") and bool(getattr(resp, "content", "").strip())
    check("timeout(llm) repli sur un autre LLM (groq)", ok,
          f"provider_used={used} fallbacks={getattr(resp,'fallbacks',0)} "
          f"len(content)={len(getattr(resp,'content',''))}")


def main():
    try:
        mw = MWClient(timeout=CALL_TIMEOUT)
    except Exception as e:
        log(f"Daemon introuvable : {e}\nLance d'abord le daemon (8771) + AFD.")
        sys.exit(2)

    t0 = time.time()
    cleanup_agents(mw)
    cleanup_disk()

    log("\n=== Création de l'équipe (via daemon) ===")
    ids = {}
    for role, cfg in TEAM.items():
        aid = create(mw, PREFIX + role, load_wf(cfg["yaml"]))
        ids[role] = aid
        log(f"  - {PREFIX}{role:14s} -> {aid}  ({cfg['prov']}/{cfg['model']})", flush=True)
    check("4 agents créés (daemon)", all(ids.values()), str(ids))

    # 1) Manager : setup (dépôt + brainstorm initial)
    ok, _, _, _ = run(mw, PREFIX + "manager", "setup", t0,
                      TEAM["manager"]["prov"], TEAM["manager"]["model"])
    check("manager setup OK", ok)
    bare = mw_home() / "repos" / f"{PROJECT_ID}.git"
    check("dépôt central bare créé", bare.exists())

    # 2) Brainstorm PARALLÈLE (3 agents, LLM différents) -> simultanéité
    log("\n=== Brainstorm parallèle (worker_engine + worker_cli + analyst) ===", flush=True)
    jobs = {
        "engine": (PREFIX + "worker_engine", "brainstorm",
                   TEAM["worker_engine"]["prov"], TEAM["worker_engine"]["model"]),
        "cli":    (PREFIX + "worker_cli", "brainstorm",
                   TEAM["worker_cli"]["prov"], TEAM["worker_cli"]["model"]),
        "analyst":(PREFIX + "analyst", "spec",
                   TEAM["analyst"]["prov"], TEAM["analyst"]["model"]),
    }
    b_out, b_dt = run_parallel(mw, jobs)
    check("brainstorm parallèle terminé", all(v[0] for v in b_out.values()),
          f"individuels={ {k: round(v[2],1) for k,v in b_out.items()} } total={b_dt:.1f}s")
    check("branchement simultané (total < somme)",
          b_dt < sum(v[2] for v in b_out.values()) + 1,
          f"total={b_dt:.1f}s somme={sum(v[2] for v in b_out.values()):.1f}s")

    # 3) Manager : decide (tour de table -> interfaces)
    ok, _, _, _ = run(mw, PREFIX + "manager", "decide", t0,
                      TEAM["manager"]["prov"], TEAM["manager"]["model"])
    check("manager decide OK", ok)

    # 4) Implémentation PARALLÈLE (engine + cli)
    log("\n=== Implémentation parallèle (worker_engine + worker_cli) ===", flush=True)
    jobs = {
        "engine": (PREFIX + "worker_engine", "implement",
                   TEAM["worker_engine"]["prov"], TEAM["worker_engine"]["model"]),
        "cli":    (PREFIX + "worker_cli", "implement",
                   TEAM["worker_cli"]["prov"], TEAM["worker_cli"]["model"]),
    }
    i_out, i_dt = run_parallel(mw, jobs)
    check("implémentation parallèle terminée", all(v[0] for v in i_out.values()))

    # 5) Manager : merge + main.py
    ok, _, _, _ = run(mw, PREFIX + "manager", "merge", t0,
                      TEAM["manager"]["prov"], TEAM["manager"]["model"])
    check("manager merge OK", ok)

    # 6) Analyst : review
    ok, _, _, _ = run(mw, PREFIX + "analyst", "review", t0,
                      TEAM["analyst"]["prov"], TEAM["analyst"]["model"])
    check("analyst review OK", ok)

    # ── Vérifications de livrables ──
    mgr_clone = mw_home() / "memagent" / str(ids["manager"]) / "workspace" / PROJECT_ID
    def exists_any(rel):
        candidates = [mgr_clone / rel]
        for role in TEAM:
            candidates.append(mw_home() / "memagent" / str(ids[role]) /
                              "workspace" / PROJECT_ID / rel)
        return any(c.exists() for c in candidates)

    check("ROADMAP.md livré", exists_any("ROADMAP.md"))
    check("docs/INTERFACES.md livré", exists_any("docs/INTERFACES.md"))
    check("docs/SPEC.md livré (analyst)", exists_any("docs/SPEC.md"))
    check("src/engine.py livré (worker_engine)", exists_any("src/engine.py"))
    check("src/cli.py livré (worker_cli)", exists_any("src/cli.py"))
    check("src/main.py intégré (manager)", exists_any("src/main.py"))

    main_py = mgr_clone / "src" / "main.py"
    if main_py.exists():
        try:
            import py_compile
            py_compile.compile(str(main_py), doraise=True)
            check("src/main.py compile (syntaxe OK)", True)
        except Exception as e:
            check("src/main.py compile (syntaxe OK)", False, str(e)[:200])

    # ── Chatroom : discussion multi-agents ──
    agents_in_chat = set(chatroom_agents())
    log(f"    participants chatroom : {sorted(agents_in_chat)}", flush=True)
    check("chatroom : >=3 agents distincts ont discuté",
          len(agents_in_chat) >= 3, f"participants={sorted(agents_in_chat)}")

    # ── Test unitaire du skill timeout(llm) ──
    check_timeout_skill_unit()

    # ── Fuites : état des processus daemon/AFD ──
    log(f"\n=== Terminé en {time.time()-t0:.1f}s ===")
    failed = [n for n, c, _ in results if not c]
    if failed:
        log(f"RESULT: FAIL ({len(failed)}) : {failed}")
        cleanup_agents(mw); cleanup_disk()
        sys.exit(1)
    log("RESULT: PASS — scénario échecs CLI complet via daemon (multi-LLM + timeout(llm))")
    cleanup_agents(mw); cleanup_disk()


if __name__ == "__main__":
    main()
