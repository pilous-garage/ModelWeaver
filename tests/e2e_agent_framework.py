#!/usr/bin/env python3
"""E2E — Framework Agent ModelWeaver (Phases 1→5).

Test « vrai » piloté via le client HTTP réel (MWClient) contre le daemon
démarré dans le conteneur. Tous les appels LLM passent par Ollama (forwardé
depuis l'hôte) — modèle léger par défaut. Un test « cloud » (GROQ) est
tenté en best-effort si une clé a été onboardée.

Sortie : résumé PASS/FAIL ; exit != 0 si au moins un FAIL.
"""
import os
import sys
import json
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.api.client import MWClient

LM_PROVIDER = "ollama"
LM_MODEL = "qwen2.5:0.5b"   # léger + en cache sur l'hôte
PREFIX = "e2e_"

results: list = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    line = f"[{mark}] {name}"
    if not cond and detail:
        line += f"\n      -> {detail}"
    print(line, flush=True)


def cleanup(mw):
    try:
        data = mw.agent.list()
        for a in data.get("agents", []):
            if a["name"].startswith(PREFIX):
                try:
                    mw.agent.delete(name=a["name"])
                except Exception:
                    pass
    except Exception:
        pass
    try:
        data = mw.chat.session.list()
        for s in data.get("sessions", []):
            if s["name"].startswith(PREFIX):
                try:
                    mw.chat.session.delete(name=s["name"])
                except Exception:
                    pass
    except Exception:
        pass


def get_agent(mw, name):
    lst = mw.agent.list().get("agents", [])
    return next((a for a in lst if a["name"] == name), None)


def run_execute(mw, name, request):
    """Exécute agent.execute dans un thread (les signaux bloquent)."""
    holder = {}
    def _t():
        try:
            holder["res"] = mw.agent.execute(name=name, request=request)
        except Exception as e:  # MWError remonté
            holder["res"] = {"status": "error", "error": str(e)}
    th = threading.Thread(target=_t, daemon=True)
    th.start()
    return th, holder


# ───────────────────────────────────────────────
def main():
    mw = MWClient()
    cleanup(mw)

    # ===== PHASE 1 : lifecycle + exécution directe (Bridge) =====
    print("\n=== PHASE 1 : lifecycle + Bridge ===")
    r = mw.agent.create(name="e2e_p1", role="tester",
                        occupation="noncontinue", resources={"llm": True})
    check("P1 create", r.get("status") == "ok" and r.get("agent_id"), str(r))
    aid = r.get("agent_id")
    lst = mw.agent.list().get("agents", [])
    check("P1 list", any(a["name"] == "e2e_p1" for a in lst))
    g = mw.agent.get(name="e2e_p1")
    check("P1 get", (g.get("agent") or {}).get("name") == "e2e_p1", str(g)[:120])

    res = mw.agent.execute(name="e2e_p1",
                           request="Réponds en un seul mot français: bonjour",
                           provider_ref=LM_PROVIDER, model_ref=LM_MODEL)
    content = (res or {}).get("content", "")
    check("P1 execute (LLM local)", bool(content), str(res)[:160])
    me = get_agent(mw, "e2e_p1")
    check("P1 endort après exécution", me is not None and me.get("running") is False,
          f"running={me.get('running') if me else 'NONE'}")

    # ===== PHASE 2 : workflow FSM (set_variable / switch / llm_call / end) =====
    print("\n=== PHASE 2 : FSM workflow ===")
    wf = {"steps": [
        {"id": "sv", "type": "set_variable", "name": "lang", "value": "fr", "next": "sw"},
        {"id": "sw", "type": "switch", "variable": "lang",
         "conditions": [{"operator": "EQUALS", "value": "fr", "next": "fr"},
                        {"operator": "EQUALS", "value": "en", "next": "en"}],
         "default": "fr"},
        {"id": "fr", "type": "llm_call", "request": "Traduis '{lang}' en anglais, un seul mot.",
         "provider_ref": LM_PROVIDER, "model_ref": LM_MODEL, "output_capture": "out_fr", "next": "end"},
        {"id": "en", "type": "llm_call", "request": "Say hello.",
         "provider_ref": LM_PROVIDER, "model_ref": LM_MODEL, "output_capture": "out_en", "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]}
    mw.agent.create(name="e2e_p2", role="fsm", occupation="noncontinue",
                    config={"workflow": wf})
    res2 = mw.agent.execute(name="e2e_p2", request="go")
    vars2 = (res2 or {}).get("variables", {})
    check("P2 execute ok", (res2 or {}).get("status") in ("ok", "SUCCESS", "success"), str(res2)[:160])
    check("P2 set_variable", vars2.get("lang") == "fr", str(vars2))
    check("P2 branche fr (switch)", bool(vars2.get("out_fr")), str(vars2))
    check("P2 pas branche en", not vars2.get("out_en"), str(vars2))

    # ===== PHASE 3 : ressources, préemption, zombies =====
    print("\n=== PHASE 3 : ressources & préemption ===")
    mw.agent.create(name="e2e_imp", role="r",
                    resources={"llm": True, "ram": {"min_gb": 99999}})
    ev = mw.agent.resources.evaluate(name="e2e_imp")
    check("P3 évaluation impossible (RAM)", ev.get("possible") is False, str(ev)[:160])
    check("P3 raison RAM présente",
          any("RAM" in (x or "") for x in ev.get("reasons", [])), str(ev.get("reasons")))

    mw.agent.create(name="e2e_ok", role="r", resources={"llm": True})
    ev2 = mw.agent.resources.evaluate(name="e2e_ok")
    check("P3 évaluation possible", ev2.get("possible") is True, str(ev2)[:160])
    check("P3 LLM alloué (ollama détecté)",
          (ev2.get("llm") or {}).get("allocated") is True, str(ev2.get("llm")))

    # Zombie : on insère un runtime actif avec heartbeat périmé (simulé).
    # pid = 999999 (sentinel) : processus inexistant -> le kill sera un no-op
    # sûr (on ne veut pas tuer le daemon de test). status='RUNNING' requis
    # pour que check_heartbeats le considère comme agent actif.
    from modules.sql.db import AgentsDB
    db = AgentsDB()
    zid = db.conn.execute("SELECT agent_id FROM agents WHERE name='e2e_z'").fetchone()
    if not zid:
        mw.agent.create(name="e2e_z", role="r", occupation="noncontinue",
                        resources={"llm": True})
        zid = db.conn.execute("SELECT agent_id FROM agents WHERE name='e2e_z'").fetchone()
    zid = zid[0]
    db.conn.execute("UPDATE agents SET status='RUNNING' WHERE agent_id=?", (zid,))
    db.conn.execute(
        "INSERT OR REPLACE INTO agent_runtime "
        "(agent_id, thread_id, pid, heartbeat_at, started_at, current_step) "
        "VALUES (?,?,?,?,?,?)",
        (zid, f"t:{zid}", 999999, "2000-01-01 00:00:00",
         "2000-01-01 00:00:00", "z"))
    db.conn.commit()
    st = mw.agent.manager.status()
    check("P3 zombie détecté", zid in (st.get("zombies") or []), str(st))
    db.conn.execute("DELETE FROM agent_runtime WHERE agent_id=?", (zid,))
    db.conn.commit()

    # Préemption : candidat impossible (RAM) mais haute priorité -> doit
    # préempter un agent actif préemptible de priorité inférieure.
    mw.agent.create(name="e2e_hi", role="high", occupation="noncontinue",
                    resources={"llm": True, "priority": 10, "preemptible": False,
                               "ram": {"min_gb": 99999}})
    mw.agent.create(name="e2e_lo", role="low", occupation="noncontinue",
                    resources={"llm": True, "priority": 1, "preemptible": True})
    loid = db.conn.execute("SELECT agent_id FROM agents WHERE name='e2e_lo'").fetchone()[0]
    db.conn.execute("UPDATE agents SET status='RUNNING' WHERE agent_id=?", (loid,))
    db.conn.execute(
        "INSERT INTO agent_runtime "
        "(agent_id, thread_id, pid, heartbeat_at, started_at, current_step) "
        "VALUES (?,?,?,datetime('now'),datetime('now'),'run')",
        (loid, f"t:{loid}", 999999))
    db.conn.commit()
    ad = mw.agent.admit(name="e2e_hi")
    check("P3 préemption déclenchée", len(ad.get("preempted") or []) >= 1, str(ad))
    after = get_agent(mw, "e2e_lo")
    check("P3 agent préempté stoppé", after is None or after.get("running") is False,
          f"running={after.get('running') if after else 'NONE'}")
    db.conn.execute("DELETE FROM agent_runtime WHERE agent_id=?", (loid,))
    db.conn.commit()

    # ===== PHASE 4 : signaux + streaming =====
    print("\n=== PHASE 4 : signaux & streaming ===")
    # 4a. Streaming : exécution puis lecture du buffer.
    wf_s = {"steps": [
        {"id": "c", "type": "llm_call", "request": "Explique le phénomène léger en une phrase.",
         "provider_ref": LM_PROVIDER, "model_ref": LM_MODEL, "output_capture": "o", "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]}
    rc = mw.agent.create(name="e2e_stream", role="r", occupation="noncontinue",
                         config={"workflow": wf_s})
    mw.agent.execute(name="e2e_stream", request="go")
    sres = mw.agent.stream(agent_id=rc["agent_id"], seq=0)
    chunks = sres.get("chunks") or []
    check("P4 streaming buffer non vide", len(chunks) >= 1, str(sres)[:160])

    # 4b. Configure (injection de variables appliquée pendant l'exécution
    #     puis persistée dans variables_json).
    wf_cf = {"steps": [
        {"id": "c", "type": "llm_call", "request": "Dis un mot contenant {city}.",
         "provider_ref": LM_PROVIDER, "model_ref": LM_MODEL, "output_capture": "o", "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]}
    mw.agent.create(name="e2e_cf", role="r", occupation="noncontinue",
                    config={"workflow": wf_cf})
    mw.agent.signal(agent_id=get_agent(mw, "e2e_cf")["agent_id"],
                    type="configure", payload={"variables": {"city": "Paris"}})
    mw.agent.execute(name="e2e_cf", request="go")
    gcf = mw.agent.get(name="e2e_cf")
    vcf = json.loads((gcf.get("agent") or {}).get("variables_json") or "{}")
    check("P4 configure appliqué + persisté", vcf.get("city") == "Paris", str(vcf))

    # 4c. Pause/Resume (signal vu au 1er signal_check -> blocage -> resume).
    wf_pr = {"steps": [
        {"id": "a", "type": "llm_call", "request": "Phrase courte.",
         "provider_ref": LM_PROVIDER, "model_ref": LM_MODEL, "output_capture": "x", "next": "b"},
        {"id": "b", "type": "llm_call", "request": "Une autre phrase.",
         "provider_ref": LM_PROVIDER, "model_ref": LM_MODEL, "output_capture": "y", "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]}
    mw.agent.create(name="e2e_pr", role="r", occupation="noncontinue",
                    config={"workflow": wf_pr})
    pr_id = get_agent(mw, "e2e_pr")["agent_id"]
    mw.agent.signal(agent_id=pr_id, type="pause")
    th, holder = run_execute(mw, "e2e_pr", "go")
    time.sleep(0.8)
    mw.agent.signal(agent_id=pr_id, type="resume")
    th.join(timeout=90)
    resp = holder.get("res") or {}
    check("P4 pause/resume -> succès", resp.get("status") in ("ok", "SUCCESS", "success"),
          str(resp)[:160])

    # 4d. Kill via canal de signaux (signal_check lève AgentAbort).
    mw.agent.create(name="e2e_k", role="r", occupation="noncontinue",
                    config={"workflow": wf_pr})
    k_id = get_agent(mw, "e2e_k")["agent_id"]
    mw.agent.signal(agent_id=k_id, type="kill")
    th2, holder2 = run_execute(mw, "e2e_k", "go")
    th2.join(timeout=90)
    respk = holder2.get("res") or {}
    check("P4 kill -> avorté", respk.get("status") in ("aborted", "failed", "error"),
          str(respk)[:160])

    # ===== PHASE 5 : occupation disparate + spawn + handoff =====
    print("\n=== PHASE 5 : orchestration ===")
    child_wf = {"steps": [
        {"id": "c", "type": "llm_call", "request": "Donne un nom de chat en un mot.",
         "provider_ref": LM_PROVIDER, "model_ref": LM_MODEL, "output_capture": "ans", "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]}
    # 5a. Spawn à la demande (occupation disparate) -> exécute puis dort.
    sp = mw.agent.spawn(name="e2e_disp", role="assistant", request="go",
                        occupation="disparate", config=child_wf,
                        provider_ref=LM_PROVIDER, model_ref=LM_MODEL)
    sp_content = ((sp or {}).get("result") or {}).get("content", "")
    check("P5 spawn disparate (LLM)", bool(sp_content), str(sp)[:160])
    check("P5 spawn endort (sleeping)", (sp or {}).get("sleeping") is True, str(sp)[:120])
    disp = get_agent(mw, "e2e_disp")
    check("P5 spawn pas actif (sommeil BDD)",
          disp is not None and disp.get("running") is False,
          f"running={disp.get('running') if disp else 'NONE'}")

    # 5b. FSM spawn step : parent crée un enfant et capture sa sortie.
    parent_wf = {"steps": [
        {"id": "sp", "type": "spawn", "name": "e2e_child", "role": "child",
         "occupation": "disparate", "config": child_wf, "request": "go",
         "output_capture": "child_out", "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]}
    mw.agent.create(name="e2e_parent", role="orchestrator", occupation="noncontinue",
                    config={"workflow": parent_wf})
    rpar = mw.agent.execute(name="e2e_parent", request="go")
    vpar = (rpar or {}).get("variables", {})
    check("P5 FSM spawn capture enfant", bool(vpar.get("child_out")), str(vpar))
    child = get_agent(mw, "e2e_child")
    check("P5 enfant créé + endormi",
          child is not None and child.get("running") is False,
          f"running={child.get('running') if child else 'NONE'}")

    # 5c. Handoff / succession (route explicite).
    #     e2e_from produit une variable (set_variable) puis on transfère.
    wf_from = {"steps": [
        {"id": "sv", "type": "set_variable", "name": "handoff_key", "value": "VAL", "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]}
    mw.agent.create(name="e2e_from", role="from", occupation="noncontinue",
                    config={"workflow": wf_from})
    mw.agent.create(name="e2e_to", role="to", occupation="noncontinue",
                    resources={})
    mw.agent.execute(name="e2e_from", request="go")  # persiste handoff_key
    ho = mw.agent.handoff(from_name="e2e_from", to_name="e2e_to")
    check("P5 handoff ok", ho.get("status") == "ok", str(ho)[:160])
    check("P5 handoff transporte variables", (ho.get("carried_variables") or 0) >= 1, str(ho))
    gto = mw.agent.get(name="e2e_to")
    vto = json.loads((gto.get("agent") or {}).get("variables_json") or "{}")
    check("P5 successeur reçoit variables", vto.get("handoff_key") == "VAL", str(vto))

    # ===== BONUS : fournisseur cloud (GROQ) si clé onboardée =====
    print("\n=== BONUS : cloud (GROQ) ===")
    keys = mw.keys.list().get("keys", [])
    has_groq = any(k.get("provider_ref") == "groq" for k in keys)
    if has_groq:
        try:
            rg = mw.agent.execute(name="e2e_p1", request="Say hi in one word.",
                                  provider_ref="groq", model_ref="llama-3.1-8b-instant")
            cg = (rg or {}).get("content", "")
            if cg:
                check("BONUS groq cloud", True)
            else:
                check("BONUS groq cloud", False, f"contenu vide: {str(rg)[:120]}")
        except Exception as e:
            print(f"[SKIP] BONUS groq cloud (réseau/clé conteneur): {e}")
    else:
        print("[SKIP] BONUS groq cloud (pas de clé groq onboardée)")

    # ===== PHASE 6 : Chat Service (V0.6.6) — sessions = agents =====
    print("\n=== PHASE 6 : Chat Service ===")
    cA = "e2e_chatA"
    cB = "e2e_chatB"
    ra = mw.chat.session.create(name=cA, provider_ref=LM_PROVIDER, model_ref=LM_MODEL,
                                allow_read_others=True)
    check("P6 session A créée", ra.get("status") == "ok" and ra.get("agent_id"), str(ra))
    rb = mw.chat.session.create(name=cB, provider_ref=LM_PROVIDER, model_ref=LM_MODEL,
                                allow_read_others=False)
    check("P6 session B créée", rb.get("status") == "ok", str(rb))
    lst6 = mw.chat.session.list().get("sessions", [])
    check("P6 liste 2 sessions", sum(1 for s in lst6 if s["name"] in (cA, cB)) == 2, str(lst6))

    sa = mw.chat.session.send(name=cA, message="Réponds en un seul mot: chat", stream=True)
    check("P6 A répond (LLM local)", bool((sa or {}).get("reply")), str(sa)[:160])
    # streaming buffer peuplé pendant la génération
    sst = mw.chat.session.stream(name=cA, seq=0)
    check("P6 A stream buffer non vide", (sst or {}).get("count", 0) >= 1, str(sst)[:120])
    ha = mw.chat.session.history(name=cA).get("messages", [])
    check("P6 A historique (user+assistant)", len(ha) >= 2 and ha[0]["role"] == "user",
          f"len={len(ha)}")

    sb = mw.chat.session.send(name=cB, message="Dis un chiffre en un mot.")
    check("P6 B répond", bool((sb or {}).get("reply")), str(sb)[:160])

    # read-others : A autorisé lit B ; B non autorisé ne peut pas lire A
    rd_ok = mw.chat.session.read(name=cA, other=cB)
    check("P6 A lit B (autorisé)", (rd_ok or {}).get("status") == "ok"
          and len(rd_ok.get("messages", [])) >= 1, str(rd_ok)[:120])
    rd_ko = mw.chat.session.read(name=cB, other=cA)
    check("P6 B refusé de lire A", (rd_ko or {}).get("status") == "error", str(rd_ko)[:120])

    dela = mw.chat.session.delete(name=cA)
    delb = mw.chat.session.delete(name=cB)
    check("P6 suppression sessions", (dela or {}).get("status") == "ok"
          and (delb or {}).get("status") == "ok", f"{dela} {delb}")

    # ===== PHASE 7 : routage dynamique des agents (Agent Framework Daemon) =====
    print("\n=== PHASE 7 : routage dynamique agents ===")
    # Catalogue des capacités (rôles + skills)
    sc, bc = mw.request_raw("GET", "capabilities")
    roles7 = (bc.get("result") or {}).get("roles", {})
    check("P7 capabilities (200 + rôles)", sc == 200 and "assistant" in roles7, str(bc)[:160])

    # Crée un agent rôle assistant (skills: chat, research, summarize, search)
    ca = mw.agent.create(name="e2e_dyn", role="assistant", occupation="noncontinue")
    check("P7 agent assistant créé", (ca or {}).get("status") == "ok" and ca.get("agent_id"), str(ca))
    aid7 = ca.get("agent_id")

    # Introspection : routes résolues à runtime depuis le rôle
    sr, br = mw.request_raw("GET", f"agents/{aid7}/routes")
    ops7 = [(r.get("op")) for r in (br.get("result") or {}).get("routes", [])]
    check("P7 routes reflètent le rôle (chat/research/summarize/search)",
          {"chat", "research", "summarize", "search"}.issubset(set(ops7)), str(ops7))

    # Op autorisée par le rôle -> exécution LLM
    sok, bok = mw.request_raw("POST", f"agents/{aid7}/research",
                              message="Donne un fait sur Paris en une phrase.",
                              provider_ref=LM_PROVIDER, model_ref=LM_MODEL)
    check("P7 op autorisée exécute (research 200)", sok == 200
          and (bok.get("result") or {}).get("status") == "ok", f"{sok} {str(bok)[:160]}")

    # Op hors-rôle -> 403 not_capable (code_gen n'est pas un skill d'assistant)
    sk, bk = mw.request_raw("POST", f"agents/{aid7}/code_gen",
                            message="x", provider_ref=LM_PROVIDER, model_ref=LM_MODEL)
    check("P7 op hors-rôle refusée (code_gen 403 not_capable)",
          sk == 403 and (bk.get("result") or {}).get("reason") == "not_capable",
          f"{sk} {str(bk)[:160]}")

    # Op inconnue -> 404 unknown
    su, bu = mw.request_raw("POST", f"agents/{aid7}/frobnicate", message="x")
    check("P7 op inconnue (404 unknown)", su == 404
          and (bu.get("result") or {}).get("reason") == "unknown", f"{su} {str(bu)[:160]}")

    mw.agent.delete(name="e2e_dyn")

    # ===== PHASE 8 : stockage disque propriétaire par agent (memagent) =====
    print("\n=== PHASE 8 : stockage disque (memagent) ===")
    # Création auto nom role_N
    ca8 = mw.agent.create(name="", role="assistant")
    check("P8 auto-name (assistant_N)", (ca8 or {}).get("status") == "ok"
          and (ca8 or {}).get("ref","").startswith("agent:assistant_"), str(ca8))
    aid8 = ca8.get("agent_id")
    # Vérifier le dossier existe (via HTTP storage route)
    sr8, br8 = mw.request_raw("GET", f"agents/{aid8}/storage")
    check("P8 storage GET 200 + max=10Mo", sr8 == 200
          and br8.get("result",{}).get("max_bytes") == 10*1024*1024, str(br8)[:120])
    # Écrire un fichier via AgentStorage
    from AgentFrameWork.agent_storage import AgentStorage
    from modules.sql.db import AgentsDB
    st8 = AgentStorage(aid8, AgentsDB().conn)
    st8.write("work", "test.txt", "hello")
    # GET storage -> used > 0
    sr8b, br8b = mw.request_raw("GET", f"agents/{aid8}/storage")
    check("P8 used_bytes > 0 après écriture", sr8b == 200
          and br8b.get("result",{}).get("used_bytes",0) > 0, f"used={br8b.get('result',{}).get('used_bytes')}")
    # Demande d'augmentation quota
    st8.request_quota_increase(50*1024*1024)
    sr8c, br8c = mw.request_raw("GET", f"agents/{aid8}/storage")
    qr = br8c.get("result",{}).get("quota_request")
    check("P8 quota_request pending", qr is not None and qr.get("status") == "pending", str(qr))
    # Approuver
    sr8d, br8d = mw.request_raw("POST", f"agents/{aid8}/storage/quota/approve",
                                max_bytes=100*1024*1024)
    check("P8 approve 200", sr8d == 200
          and br8d.get("result",{}).get("max_bytes") == 100*1024*1024, str(br8d)[:120])
    # Vérifier request cleared
    sr8e, br8e = mw.request_raw("GET", f"agents/{aid8}/storage")
    check("P8 quota_request cleared après approve",
          br8e.get("result",{}).get("quota_request") is None, str(br8e)[:120])
    # Suppression agent → dossier détruit
    mw.agent.delete(agent_id=aid8)
    from pathlib import Path
    check("P8 dossier détruit après delete agent", not Path(st8.root).exists(), str(st8.root))

    # ===== Résumé =====
    cleanup(mw)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    failed = total - passed
    print("\n" + "=" * 50)
    print(f"RÉSUMÉ E2E : {passed}/{total} PASS, {failed} FAIL")
    print("=" * 50)
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL -> {name} | {detail}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
