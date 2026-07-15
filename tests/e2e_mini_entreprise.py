#!/usr/bin/env python3
"""E2E — « Mini-entreprise » ModelWeaver (V0.6.22).

Test *statique* (sans LLM, donc reproductible) d'une équipe d'agents qui
collabore pour coder un mini-jeu, avec le modèle de collaboration :

  - Dépôt central BARE (source de vérité) : mw_home()/repos/{project}.git
  - Chaque agent CLONE le dépôt dans son workspace perso
    (memagent/{agent}/workspace/{project}) : le travail versionné vit LÀ.
    Les dossiers privés (perso/ ctx/ mem/ history/) restent hors du clone.
  - Réseau 1 (1:1)  : message_send / message_recv (inbox par agent)
  - Réseau 2 (N:N)  : chatroom_post / chatroom_read (comms/{chatroom_id})
  - Espace commun live (non versionné) : common/{group_id}

Rôles :
  - manager : repo_init + clone, ROADMAP, assigne, merge branches, intègre main
  - analyst : clone, rédige la spec, review (ne code pas)
  - worker1 : clone, code src/logic.py sur branche feature-logic, push
  - worker2 : clone, code src/ui.py  sur branche feature-ui, push

Sortie : résumé PASS/FAIL ; exit != 0 si au moins un FAIL.
"""
import os
import sys
import shutil
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.api.client import MWClient
from services._common import mw_home

PROJECT_ID = "minigame"
CHATROOM_ID = "minigame-team"
GROUP_ID = "minigame-team"
PREFIX = "me_"
results: list = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    line = f"[{mark}] {name}"
    if not cond and detail:
        line += f"\n      -> {detail}"
    print(line, flush=True)


LOGIC_PY = '''\
def check(guess, secret):
    """Compare une proposition au nombre secret."""
    if guess == secret:
        return "win"
    return "high" if guess > secret else "low"
'''

UI_PY = '''\
def render(result):
    """Traduit un résultat de logique en message joueur."""
    return {"win": "Bravo !", "high": "Trop grand", "low": "Trop petit"}[result]
'''

MAIN_PY = '''\
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from logic import check
from ui import render


def play(secret, guesses):
    return [render(check(g, secret)) for g in guesses]


if __name__ == "__main__":
    out = play(7, [5, 9, 7])
    print(out)
    assert out[-1] == "Bravo !", out
    print("OK")
'''


# ── Helpers workflow ──
def sw(conds, default):
    return {"id": "start", "type": "switch", "variable": "request",
            "conditions": conds, "default": default}


def call(sid, fn, inputs, nxt, capture=None):
    step = {"id": sid, "type": "call", "fn": fn, "inputs": inputs, "next": nxt}
    if capture:
        step["capture"] = capture
    return step


END = {"id": "end", "type": "end", "status": "SUCCESS"}
P = {"project_id": PROJECT_ID}


def manager_wf():
    return {"steps": [
        sw([{"operator": "EQUALS", "value": "assign", "next": "a1"},
            {"operator": "EQUALS", "value": "merge", "next": "m1"}], "a1"),
        # ── phase assign : central repo + clone + roadmap ──
        call("a1", "system/repo_init@v1", dict(P), "a2"),
        call("a2", "system/git_clone@v1", dict(P), "a3"),
        call("a3", "system/project_write@v1",
             {**P, "path": "ROADMAP.md",
              "content": "# Roadmap\\n- logic.py (worker1)\\n- ui.py (worker2)\\n- main.py (manager)\\n"},
             "a4"),
        call("a4", "system/project_write@v1",
             {**P, "path": "important/DECISIONS.md",
              "content": "# Decisions\\nArchi: logic + ui + main.\\n"}, "a5"),
        call("a5", "system/git_commit@v1", {**P, "message": "roadmap + decisions"}, "a6"),
        call("a6", "system/git_push@v1", dict(P), "a7"),
        # tableau de bord live (espace commun, non versionné)
        call("a7", "system/common_write@v1",
             {"group_id": GROUP_ID, "path": "STATUS.md",
              "content": "assign: en cours\\n"}, "a8"),
        call("a8", "system/message_send@v1",
             {"to_agent_id": "{{w1_id}}", "content": "Code src/logic.py"}, "a9"),
        call("a9", "system/message_send@v1",
             {"to_agent_id": "{{w2_id}}", "content": "Code src/ui.py"}, "a10"),
        call("a10", "system/chatroom_post@v1",
             {"chatroom_id": CHATROOM_ID, "content": "[manager] taches assignees"}, "end"),
        # ── phase merge : fetch + merge branches + intègre main.py ──
        call("m1", "system/git_fetch@v1", dict(P), "m2"),
        call("m2", "system/git_pull@v1", {**P, "branch": "master"}, "m3"),
        call("m3", "system/git_merge@v1", {**P, "name": "origin/feature-logic"}, "m4"),
        call("m4", "system/git_merge@v1", {**P, "name": "origin/feature-ui"}, "m5"),
        call("m5", "system/project_write@v1",
             {**P, "path": "src/main.py", "content": MAIN_PY}, "m6"),
        call("m6", "system/git_commit@v1", {**P, "message": "integrate main.py"}, "m7"),
        call("m7", "system/git_push@v1", dict(P), "m8"),
        call("m8", "system/message_send@v1",
             {"to_agent_id": "{{analyst_id}}", "content": "Merci de reviewer main.py"}, "m9"),
        call("m9", "system/chatroom_post@v1",
             {"chatroom_id": CHATROOM_ID, "content": "[manager] integration faite"}, "end"),
        END,
    ]}


def analyst_wf():
    return {"steps": [
        sw([{"operator": "EQUALS", "value": "spec", "next": "s1"},
            {"operator": "EQUALS", "value": "review", "next": "r1"}], "s1"),
        # ── phase spec ──
        call("s1", "system/git_clone@v1", dict(P), "s2"),
        call("s2", "system/project_write@v1",
             {**P, "path": "docs/SPEC.md",
              "content": "# Spec\\nJeu: deviner un nombre 1-10. Modules logic + ui + main.\\n"},
             "s3"),
        call("s3", "system/git_commit@v1", {**P, "message": "spec"}, "s4"),
        call("s4", "system/git_push@v1", dict(P), "s5"),
        call("s5", "system/chatroom_post@v1",
             {"chatroom_id": CHATROOM_ID, "content": "[analyst] spec prete"}, "s6"),
        call("s6", "system/message_send@v1",
             {"to_agent_id": "{{manager_id}}", "content": "Spec prete"}, "end"),
        # ── phase review ──
        call("r1", "system/git_fetch@v1", dict(P), "r2"),
        call("r2", "system/git_pull@v1", {**P, "branch": "master"}, "r3"),
        call("r3", "system/project_read@v1",
             {**P, "path": "src/main.py"}, "r4", capture={"content": "reviewed"}),
        call("r4", "system/common_read@v1",
             {"group_id": GROUP_ID, "path": "STATUS.md"}, "r5",
             capture={"content": "status"}),
        call("r5", "system/chatroom_post@v1",
             {"chatroom_id": CHATROOM_ID, "content": "[analyst] review OK"}, "r6"),
        call("r6", "system/message_send@v1",
             {"to_agent_id": "{{manager_id}}", "content": "LGTM"}, "end"),
        END,
    ]}


def worker_wf(branch, path, content):
    return {"steps": [
        call("w1", "system/message_recv@v1", {}, "w2", capture={"count": "nmsg"}),
        call("w2", "system/git_clone@v1", dict(P), "w3"),
        call("w3", "system/git_branch@v1",
             {**P, "name": branch, "create": True}, "w4"),
        call("w4", "system/project_write@v1",
             {**P, "path": path, "content": content}, "w5"),
        call("w5", "system/git_commit@v1", {**P, "message": f"add {path}"}, "w6"),
        call("w6", "system/git_push@v1", {**P, "branch": branch}, "w7"),
        call("w7", "system/chatroom_post@v1",
             {"chatroom_id": CHATROOM_ID, "content": f"[{branch}] fait: {path}"}, "w8"),
        call("w8", "system/message_send@v1",
             {"to_agent_id": "{{manager_id}}", "content": f"{path} prete"}, "end"),
        END,
    ]}


def cleanup_disk():
    for sub in ("repos", "comms", "common", "inbox"):
        d = mw_home() / sub
        if sub == "repos":
            p = d / f"{PROJECT_ID}.git"
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
        else:
            if sub in ("comms", "common"):
                p = d / (CHATROOM_ID if sub == "comms" else GROUP_ID)
                if p.exists():
                    shutil.rmtree(p, ignore_errors=True)


def cleanup_agents(mw):
    for a in mw.agent.list().get("agents", []):
        if a["name"].startswith(PREFIX):
            try:
                mw.agent.delete(name=a["name"])
            except Exception:
                pass


def create(mw, name, wf):
    r = mw.agent.create(name=name, role="worker", occupation="noncontinue",
                        config={"workflow": wf})
    return r.get("agent_id")


def configure(mw, agent_id, variables):
    mw.agent.signal(agent_id=agent_id, type="configure",
                    payload={"variables": variables})


def run(mw, name, request):
    return mw.agent.execute(name=name, request=request)


def ok_status(res):
    return (res or {}).get("status") in ("ok", "success", "SUCCESS")


def skill(fn, **inputs):
    from services.skill_manager import call_skill
    return call_skill(f"system/{fn}@v1", inputs)


def main():
    mw = MWClient()
    cleanup_agents(mw)

    print("\n=== Création de l'équipe ===")
    mgr = create(mw, PREFIX + "manager", manager_wf())
    ana = create(mw, PREFIX + "analyst", analyst_wf())
    w1 = create(mw, PREFIX + "worker1",
                worker_wf("feature-logic", "src/logic.py", LOGIC_PY))
    w2 = create(mw, PREFIX + "worker2",
                worker_wf("feature-ui", "src/ui.py", UI_PY))
    check("agents créés", all([mgr, ana, w1, w2]),
          f"mgr={mgr} ana={ana} w1={w1} w2={w2}")

    # clones agents (au cas où un run précédent aurait laissé des restes)
    for aid in (mgr, ana, w1, w2):
        p = mw_home() / "memagent" / str(aid) / "workspace" / PROJECT_ID
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    cleanup_disk()

    # Injection des identités des pairs (réseau 1:1).
    configure(mw, mgr, {"w1_id": w1, "w2_id": w2, "analyst_id": ana})
    configure(mw, ana, {"manager_id": mgr})
    configure(mw, w1, {"manager_id": mgr})
    configure(mw, w2, {"manager_id": mgr})

    print("\n=== 1. Manager : repo central + clone + assign ===")
    r = run(mw, PREFIX + "manager", "assign")
    check("manager assign OK", ok_status(r), str(r)[:200])
    bare = mw_home() / "repos" / f"{PROJECT_ID}.git"
    check("dépôt central bare créé", bare.exists(), str(bare))
    mgr_clone = mw_home() / "memagent" / str(mgr) / "workspace" / PROJECT_ID
    check("clone manager dans workspace perso", (mgr_clone / ".git").exists(),
          str(mgr_clone))
    check("ROADMAP.md dans le clone", (mgr_clone / "ROADMAP.md").exists())
    check("important/ versionné dans le clone",
          (mgr_clone / "important" / "DECISIONS.md").exists())
    # privé : le clone ne doit PAS contenir ctx/mem/history/perso
    check("clone ne contient pas de dossiers privés",
          not (mgr_clone / "ctx").exists() and not (mgr_clone / "perso").exists())

    print("\n=== 2. Analyst : clone + spec ===")
    r = run(mw, PREFIX + "analyst", "spec")
    check("analyst spec OK", ok_status(r), str(r)[:200])
    ana_clone = mw_home() / "memagent" / str(ana) / "workspace" / PROJECT_ID
    check("docs/SPEC.md dans le clone analyst", (ana_clone / "docs" / "SPEC.md").exists())

    print("\n=== 3-4. Workers : clone + branche + push ===")
    r1 = run(mw, PREFIX + "worker1", "go")
    r2 = run(mw, PREFIX + "worker2", "go")
    check("worker1 OK", ok_status(r1), str(r1)[:200])
    check("worker2 OK", ok_status(r2), str(r2)[:200])
    check("worker1 a reçu son inbox (message_recv)",
          (r1 or {}).get("variables", {}).get("nmsg", 0) >= 1,
          str((r1 or {}).get("variables", {}).get("nmsg")))
    check("worker2 a reçu son inbox (message_recv)",
          (r2 or {}).get("variables", {}).get("nmsg", 0) >= 1,
          str((r2 or {}).get("variables", {}).get("nmsg")))
    # branches poussées sur le central
    branches = skill("git_branch", project_id=PROJECT_ID, agent_id=mgr)
    fetched = skill("git_fetch", project_id=PROJECT_ID, agent_id=mgr)
    lsr = skill("git_log", project_id=PROJECT_ID, agent_id=w1)
    check("worker1 a poussé feature-logic (commit local)",
          any("logic" in l for l in lsr.get("lines", [])), str(lsr.get("lines")))

    print("\n=== 5. Manager : fetch + merge + intègre main.py ===")
    r = run(mw, PREFIX + "manager", "merge")
    check("manager merge OK", ok_status(r), str(r)[:200])
    for f in ("src/logic.py", "src/ui.py", "src/main.py"):
        check(f"fichier mergé présent (clone manager): {f}",
              (mgr_clone / f).exists())

    print("\n=== 6. Analyst : review (pull) ===")
    r = run(mw, PREFIX + "analyst", "review")
    check("analyst review OK", ok_status(r), str(r)[:200])
    check("analyst a lu main.py (capture, après pull)",
          "def play" in (r or {}).get("variables", {}).get("reviewed", ""),
          str((r or {}).get("variables", {}).get("reviewed", ""))[:80])
    check("analyst a lu le STATUS commun (capture)",
          "assign" in (r or {}).get("variables", {}).get("status", ""),
          str((r or {}).get("variables", {}).get("status", ""))[:80])

    print("\n=== Vérifications réseaux & produit ===")
    # Réseau 3 : historique git multi-commits (via le clone manager)
    glog = skill("git_log", project_id=PROJECT_ID, agent_id=mgr)
    ncommits = len(glog.get("lines", []))
    check("git log ≥ 5 commits", ncommits >= 5, f"commits={ncommits}: {glog.get('lines')}")

    # Réseau 2 : chatroom peuplée (comms/{chatroom_id})
    chat = skill("chatroom_read", chatroom_id=CHATROOM_ID)
    check("chatroom ≥ 5 messages", chat.get("count", 0) >= 5,
          f"count={chat.get('count')}")

    # Réseau 1 : inbox du manager (analyst spec + 2 workers + analyst LGTM)
    inbox = skill("message_recv", agent_id=mgr)
    check("manager inbox ≥ 3 messages", inbox.get("count", 0) >= 3,
          f"count={inbox.get('count')}")

    # Substrat : arbre projet complet (clone manager)
    tree = skill("project_tree", project_id=PROJECT_ID, agent_id=mgr)
    files = tree.get("files", [])
    expected = {"docs/SPEC.md", "ROADMAP.md", "important/DECISIONS.md",
                "src/logic.py", "src/ui.py", "src/main.py"}
    check("arbre projet complet", expected.issubset(set(files)),
          f"manquants={expected - set(files)}")

    # Produit : le jeu tourne (exit 0) depuis le clone du manager
    proc = subprocess.run([sys.executable, "src/main.py"], cwd=str(mgr_clone),
                          capture_output=True, text=True, timeout=30)
    check("python src/main.py exit 0", proc.returncode == 0,
          f"rc={proc.returncode} out={proc.stdout} err={proc.stderr}")
    check("jeu affiche OK", "OK" in proc.stdout, proc.stdout)

    # ── Résumé ──
    cleanup_agents(mw)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    failed = total - passed
    print("\n" + "=" * 50)
    print(f"RÉSUMÉ mini-entreprise : {passed}/{total} PASS, {failed} FAIL")
    print("=" * 50)
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL -> {name} | {detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
