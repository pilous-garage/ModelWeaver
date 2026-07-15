#!/usr/bin/env python3
"""Test « live daemon multi-agent » — V0.7.0.

Parcours 100% « daemon / agent_manager » (comme une vraie interaction) :
les agents sont créés et exécutés via l'API du daemon (MWClient), donc
l'activité est visible en direct dans la GUI (panneaux Agents / Debug /
Chat). Chaque deliverable est GÉNÉRÉ par un LLM réel (Ollama local,
mistral-small:22b — aucune clef requise), contrairement à
e2e_mini_entreprise qui est statique (sans LLM).

Collaboration git (dépôt central BARE + clones persos) :
  - manager  : repo_init + clone + ROADMAP, puis fetch/merge des 3 branches
               et génération de src/main.py (LLM)
  - worker1  : branch feature-logic  -> src/logic.py (LLM) -> push
  - worker2  : branch feature-ui     -> src/ui.py    (LLM) -> push
  - analyst  : branch feature-spec   -> docs/SPEC.md (LLM) -> push, puis review

Le LLM utilisé est Ollama (local, sans clef). Pour utiliser groq, positionner
LIVE_PROVIDER/LIVE_MODEL et fournir la clef dans l'environnement du daemon.
"""
import os
import sys
import time
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.api.client import MWClient
from services._common import mw_home

PROJECT_ID = "livemg"
PREFIX = "dlive_"
PROVIDER = os.environ.get("LIVE_PROVIDER", "ollama")
MODEL = os.environ.get("LIVE_MODEL", "mistral-small:22b")

P = {"project_id": PROJECT_ID}
results: list = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    line = f"[{mark}] {name}"
    if not cond and detail:
        line += f"\n      -> {detail}"
    print(line, flush=True)


# ── Helpers workflow ──
def sw(conds, default):
    return {"id": "start", "type": "switch", "variable": "request",
            "conditions": conds, "default": default}


def call(sid, fn, inputs, nxt, capture=None, on_error=None):
    step = {"id": sid, "type": "call", "fn": fn, "inputs": inputs, "next": nxt}
    if capture:
        step["capture"] = capture
    if on_error:
        step["on_error"] = on_error
    return step


END = {"id": "end", "type": "end", "status": "SUCCESS"}


def llm(sid, prompt, capture, nxt, max_tokens=512):
    return {"id": sid, "type": "llm_call",
            "provider_ref": PROVIDER, "model_ref": MODEL,
            "skill_prompt": prompt, "output_capture": capture,
            "max_tokens": max_tokens, "next": nxt}


# ── Prompts LLM ──
LOGIC_PROMPT = (
    "You are a Python backend developer. Output ONLY the raw content of a "
    "file src/logic.py for a number-guessing game. Define a function "
    "`check(guess: int, secret: int) -> str` that returns 'win' if equal, "
    "'high' if guess>secret, else 'low'. No markdown fences, no commentary, "
    "just the Python code."
)
UI_PROMPT = (
    "You are a Python UI developer. Output ONLY the raw content of a file "
    "src/ui.py for a number-guessing game. Define `render(result: str) -> str` "
    "that maps 'win'->'Bravo !', 'high'->'Trop grand', 'low'->'Trop petit'. "
    "No markdown fences, no commentary, just the Python code."
)
MAIN_PROMPT = (
    "You are a Python integrator. Output ONLY the raw content of a file "
    "src/main.py for a number-guessing game. It must `from logic import check` "
    "and `from ui import render`, define `play(secret, guesses)` returning "
    "`[render(check(g, secret)) for g in guesses]`, and a `__main__` block "
    "that prints play(7, [5, 9, 7]) and asserts the last result is 'Bravo !'. "
    "No markdown fences, no commentary, just the Python code."
)
SPEC_PROMPT = (
    "Output ONLY the raw content of a docs/SPEC.md (Markdown) describing a "
    "number-guessing game: objective, rules, and the three modules logic.py "
    "(check), ui.py (render), main.py (play). Be concise. No markdown fences."
)


def manager_wf():
    return {"steps": [
        sw([{"operator": "EQUALS", "value": "setup", "next": "a1"},
            {"operator": "EQUALS", "value": "merge", "next": "m1"}], "a1"),
        # phase setup : dépôt central + roadmap
        call("a1", "system/repo_init@v1", dict(P), "a2"),
        call("a2", "system/git_clone@v1", dict(P), "a3"),
        call("a3", "system/project_write@v1",
             {**P, "path": "ROADMAP.md",
              "content": "# Roadmap\n- src/logic.py (worker1, LLM)\n"
                         "- src/ui.py (worker2, LLM)\n"
                         "- docs/SPEC.md (analyst, LLM)\n"
                         "- src/main.py (manager, LLM)\n"}, "a4"),
        call("a4", "system/git_commit@v1", {**P, "message": "roadmap"}, "a5"),
        call("a5", "system/git_push@v1", dict(P), "a6"),
        call("a6", "system/git_status@v1", dict(P), "end",
             capture={"clean": "_clean"}),
        # phase merge : récupère les branches + intègre main.py (LLM)
        call("m1", "system/git_fetch@v1", dict(P), "m2"),
        call("m2", "system/git_pull@v1", {**P, "branch": "master"}, "m3"),
        call("m3", "system/git_merge@v1", {**P, "name": "origin/feature-logic"}, "m4"),
        call("m4", "system/git_merge@v1", {**P, "name": "origin/feature-ui"}, "m5"),
        call("m5", "system/git_merge@v1", {**P, "name": "origin/feature-spec"}, "m6"),
        llm("m6", MAIN_PROMPT, "_main", "m7"),
        call("m7", "system/project_write@v1",
             {**P, "path": "src/main.py", "content": "{{_main}}"}, "m8"),
        call("m8", "system/git_commit@v1",
             {**P, "message": "integrate src/main.py (LLM)"}, "m9"),
        call("m9", "system/git_push@v1", dict(P), "m10"),
        call("m10", "system/git_status@v1", dict(P), "end",
             capture={"clean": "_clean"}),
        END,
    ]}


def worker_wf(branch, path, prompt):
    return {"steps": [
        call("w1", "system/git_clone@v1", dict(P), "w2"),
        call("w2", "system/git_branch@v1",
             {**P, "name": branch, "create": True}, "w3"),
        llm("w3", prompt, "_code", "w4"),
        call("w4", "system/project_write@v1",
             {**P, "path": path, "content": "{{_code}}"}, "w5"),
        call("w5", "system/git_commit@v1",
             {**P, "message": f"add {path} (LLM)"}, "w6"),
        call("w6", "system/git_push@v1", {**P, "branch": branch}, "w7"),
        call("w7", "system/git_status@v1", dict(P), "end",
             capture={"clean": "_clean"}),
        END,
    ]}


def analyst_wf():
    return {"steps": [
        sw([{"operator": "EQUALS", "value": "spec", "next": "s1"},
            {"operator": "EQUALS", "value": "review", "next": "r1"}], "s1"),
        # phase spec : SPEC.md (LLM) sur feature-spec
        call("s1", "system/git_clone@v1", dict(P), "s2"),
        call("s2", "system/git_branch@v1",
             {**P, "name": "feature-spec", "create": True}, "s3"),
        llm("s3", SPEC_PROMPT, "_spec", "s4"),
        call("s4", "system/project_write@v1",
             {**P, "path": "docs/SPEC.md", "content": "{{_spec}}"}, "s5"),
        call("s5", "system/git_commit@v1",
             {**P, "message": "spec (LLM)"}, "s6"),
        call("s6", "system/git_push@v1", {**P, "branch": "feature-spec"}, "s7"),
        call("s7", "system/git_status@v1", dict(P), "end",
             capture={"clean": "_clean"}),
        # phase review : lit main.py intégré
        call("r1", "system/git_fetch@v1", dict(P), "r2"),
        call("r2", "system/git_pull@v1", {**P, "branch": "master"}, "r3"),
        call("r3", "system/project_read@v1",
             {**P, "path": "src/main.py"}, "r4", capture={"content": "reviewed"}),
        call("r4", "system/git_status@v1", dict(P), "end",
             capture={"clean": "_clean"}),
        END,
    ]}


# ── Orchestration ──
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


def create(mw, name, wf):
    r = mw.agent.create(name=name, role="agent", occupation="noncontinue",
                        config={"workflow": wf})
    return r.get("agent_id")


def configure(mw, agent_id, variables):
    mw.agent.signal(agent_id=agent_id, type="configure",
                    payload={"variables": variables})


def run(mw, name, request, t0):
    print(f"\n--- execute {name} (request={request!r}) ---", flush=True)
    res = mw.agent.execute(name=name, request=request,
                           provider_ref=PROVIDER, model_ref=MODEL)
    ok = (res or {}).get("status") in ("ok", "success", "SUCCESS")
    print(f"    -> status={res.get('status')} "
          f"tokens={res.get('tokens_used')} "
          f"({time.time()-t0:.1f}s)", flush=True)
    return ok, res


def ollama_ready():
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags",
                                     timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    if PROVIDER == "ollama" and not ollama_ready():
        print("Ollama non disponible sur :11434 — test annulé (besoin d'un LLM).")
        sys.exit(2)

    mw = MWClient()
    t0 = time.time()
    cleanup_agents(mw)
    cleanup_disk()

    print("\n=== Création de l'équipe (via daemon) ===")
    mgr = create(mw, PREFIX + "manager", manager_wf())
    ana = create(mw, PREFIX + "analyst", analyst_wf())
    w1 = create(mw, PREFIX + "worker1",
                worker_wf("feature-logic", "src/logic.py", LOGIC_PROMPT))
    w2 = create(mw, PREFIX + "worker2",
                worker_wf("feature-ui", "src/ui.py", UI_PROMPT))
    check("agents créés (daemon)", all([mgr, ana, w1, w2]),
          f"mgr={mgr} ana={ana} w1={w1} w2={w2}")

    print("\n=== 1. Manager : repo central + ROADMAP ===")
    ok, _ = run(mw, PREFIX + "manager", "setup", t0)
    check("manager setup OK", ok)
    bare = mw_home() / "repos" / f"{PROJECT_ID}.git"
    check("dépôt central bare créé", bare.exists())
    mgr_clone = mw_home() / "memagent" / str(mgr) / "workspace" / PROJECT_ID
    check("clone manager + ROADMAP.md", (mgr_clone / "ROADMAP.md").exists())

    print("\n=== 2. Worker1 : src/logic.py (LLM) ===")
    ok, _ = run(mw, PREFIX + "worker1", "go", t0)
    check("worker1 OK", ok)
    check("src/logic.py livré (LLM)",
          (mgr_clone / "src" / "logic.py").exists() or
          (mw_home() / "memagent" / str(w1) / "workspace" / PROJECT_ID
           / "src" / "logic.py").exists())

    print("\n=== 3. Worker2 : src/ui.py (LLM) ===")
    ok, _ = run(mw, PREFIX + "worker2", "go", t0)
    check("worker2 OK", ok)
    check("src/ui.py livré (LLM)",
          (mw_home() / "memagent" / str(w2) / "workspace" / PROJECT_ID
           / "src" / "ui.py").exists())

    print("\n=== 4. Analyst : docs/SPEC.md (LLM) + branche ===")
    ok, _ = run(mw, PREFIX + "analyst", "spec", t0)
    check("analyst spec OK", ok)
    check("docs/SPEC.md livré (LLM)",
          (mw_home() / "memagent" / str(ana) / "workspace" / PROJECT_ID
           / "docs" / "SPEC.md").exists())

    print("\n=== 5. Manager : merge 3 branches + src/main.py (LLM) ===")
    ok, _ = run(mw, PREFIX + "manager", "merge", t0)
    check("manager merge OK", ok)
    main_py = mgr_clone / "src" / "main.py"
    check("src/main.py intégré (LLM)", main_py.exists())
    if main_py.exists():
        print("    --- src/main.py (extrait) ---")
        print("   ", "\n    ".join(main_py.read_text().splitlines()[:12]))

    print("\n=== 6. Analyst : review main.py intégré ===")
    ok, res = run(mw, PREFIX + "analyst", "review", t0)
    check("analyst review OK", ok)
    reviewed = (res or {}).get("variables", {}).get("reviewed", "")
    check("main.py lu par l'analyst", bool(reviewed), f"len={len(reviewed)}")

    print(f"\n=== Terminé en {time.time()-t0:.1f}s ===")
    failed = [n for n, c, _ in results if not c]
    if failed:
        print(f"RESULT: FAIL ({len(failed)} échec(s)) : {failed}")
        cleanup_agents(mw); cleanup_disk()
        sys.exit(1)
    print("RESULT: PASS — collaboration multi-agent complète via daemon/agent_manager")
    cleanup_agents(mw); cleanup_disk()


if __name__ == "__main__":
    main()
