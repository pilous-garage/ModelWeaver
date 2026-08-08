"""Routes dev-chat — chat de développement piloté par un agent maître.

Le pilote (agent de session `devchat_*`) a deux MODES gérés par la variable
`mode` :
  - plan  : READ-ONLY. Explore le code, écrit `plan.md` dans son home,
            décompose en TÂCHES workspace (explore_task, coding_task,
            test_task, review_task).
  - build : orchestre l'exécution via les tâches du workspace (les membres
            gloutons explore/coder/tester/reviewer les piochent et exécutent),
            puis vérifie la complétion.

La délégation est basée sur les TÂCHES WORKSPACE (greedy), pas call_agent.
"""

import json as _json
import threading
import time as _time
from typing import Any, Dict, Optional

from services.api.router import register, register_streaming

# Nom du pilote legacy dans la team dev-chat (référence conceptuelle).
PILOT_AGENT = "team:dev-chat/chat-pilot"
DEFAULT_WORKSPACE = "mw-dev-chat"

# Verrou par agent pilote : empêche 2 runs du MÊME agent de tourner en
# parallèle (sinon 2 threads hydratent le même agent, polluent le même
# StreamBus, et le chat mélange les fall-backs/événements des 2 runs).
# Le 2e message ATTEND la fin du 1er (sérialisation).
_PILOT_LOCKS: Dict[int, threading.Lock] = {}
_PILOT_LOCKS_GUARD = threading.Lock()


def _pilot_lock(aid: int) -> threading.Lock:
    with _PILOT_LOCKS_GUARD:
        lock = _PILOT_LOCKS.get(aid)
        if lock is None:
            lock = threading.Lock()
            _PILOT_LOCKS[aid] = lock
        return lock

# System prompts des deux modes (injectés dans le contexte du pilote).
PLAN_PROMPT = """Tu es le pilote du chat de développement (mode PLAN). READ-ONLY.
Tu ne PEUX PAS modifier le code du projet. Tu peux LIRE le code
(file/read_file@v1, shell/exec@v1 en lecture : ls/cat/grep/glob), poser des
questions (agent/ask_user@v1), et écrire UNIQUEMENT ton plan dans
{{home}}/plan.md (file/write_file@v1).

Déroulé :
1. Comprends la demande utilisateur en explorant le code.
2. Si ambigu : pose des questions (agent/ask_user@v1).
3. Écris plan.md dans ton home : objectif, fichiers à modifier, tâches.
4. Découpe le plan en TÂCHES workspace avec workspace/task_create@v1
   (workspace {{workspace_id}}, role_required : explore|coder_junior|
   coder_senior|tester|reviewer).
5. Réponds avec le résumé du plan + la liste des tâches créées.
Le mode build sera activé quand l'utilisateur approuvera le plan."""

BUILD_PROMPT = """Tu es le pilote du chat de développement (mode BUILD).
Le plan est approuvé. Tu ORCHESTRES l'exécution via les tâches du workspace
{{workspace_id}} (les membres gloutons les exécutent) :
1. Crée/lance les tâches restantes (workspace/task_create@v1, rôle adapté :
   coder_junior/coder_senior, tester, reviewer).
2. Surveille l'avancement : workspace/task_list@v1 + workspace/task_get@v1.
3. Vérifie la COMPLÉTION : une tâche doit être done avec résultat/commit ;
   sinon relance une tâche corrective.
4. Lis ton plan.md ({{home}}/plan.md) si besoin.
5. Quand tout est done, fais une synthèse et réponds.
Utilise workspace/chat_post@v1 pour communiquer avec les membres."""


def _pilot_workflow(mode: str, workspace_id: str, home: str,
                    provider_ref: str = "", model_ref: str = "") -> Dict[str, Any]:
    """Workflow FSM du pilote : mode plan ou build via un step call."""
    ctx = (PLAN_PROMPT if mode == "plan" else BUILD_PROMPT) \
        .replace("{{home}}", home).replace("{{workspace_id}}", workspace_id)
    return {
        "steps": [
            {"id": "start", "type": "call", "fn": "workflow/autonomous@v1",
             "inputs": {
                 "request": "{{request}}",
                 # Bundle PILOTE léger (~32 tools) : 68 tools (manager+dev)
                 # font exploser la latence LLM (le modèle parse les définitions
                 # ~1 min/round). Le pilote orchestre, pas besoin de tout.
                 "bundles": ["pilot"],
                 "workspace_id": workspace_id,
                 "context": ctx,
                 "max_loops": 120,
                 "stream_events": True,
                 "provider_ref": provider_ref,
                 "model_ref": model_ref,
             },
             "capture": {"stdout": "result"},
             "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]
    }


def _resolve_pilot(mgr, session: str, params: dict) -> tuple:
    """Recycle/crée l'agent pilote de la session, reconfigure le workflow du
    mode (plan/build) + provider/model. Retourne (agent_id, home, workspace)."""
    from services._common import mw_home

    workspace_id = params.get("workspace_id") or DEFAULT_WORKSPACE
    mode = params.get("mode", "plan")
    provider_ref = params.get("provider_ref", "")
    model_ref = params.get("model_ref", "")

    row = mgr.get_by_name(session)
    if not row or row.get("role_type") != "chat":
        created = mgr.create_chat_session(name=session,
                                          system_prompt=params.get("system_prompt", ""))
        if created.get("status") != "ok":
            raise RuntimeError(created.get("error", "création session échouée"))
        aid = created["agent_id"]
    else:
        aid = row["agent_id"]

    home = str(mw_home() / "agent_home" / str(aid))

    try:
        mgr.db.conn.execute(
            "UPDATE agents SET config_json=?, variables_json=? WHERE agent_id=?",
            (_json.dumps({"workflow": _pilot_workflow(mode, workspace_id, home,
                                                      provider_ref, model_ref)}),
             _json.dumps({"messages": [], "workspace_id": workspace_id,
                          "mode": mode, "home": home}), aid))
        mgr.db.conn.commit()
    except Exception as e:
        raise RuntimeError(f"reconfig pilote: {e}")

    return aid, home, workspace_id


def _run_pilot(aid: int, mode: str, message: str, workspace_id: str, home: str,
               provider_ref: str = "", model_ref: str = "", db=None) -> Dict[str, Any]:
    """Exécute le pilote de dev-chat (workflow autonomous bundles pilot).

    Sérialisé par un verrou PAR AGENT : si le pilote est déjà en cours
    (run précédent), ce message ATTEND sa fin — pas de double exécution
    parallèle (Agent.hydrate lève RuntimeError si déjà hydraté).
    """
    from services.agent_manager.service import Agent, AgentManager

    t0 = _time.time()
    lock = _pilot_lock(aid)
    acquired = lock.acquire(timeout=900)  # attend au max 15 min le run précédent
    if not acquired:
        return {"status": "error", "error": "pilote déjà en cours (timeout 15 min)",
                "mode": mode, "agent_id": aid}
    try:
        if db is None:
            db = AgentManager().db
        agent = Agent.hydrate(aid, db)
        agent._data["variables_json"] = _json.dumps({
            "messages": [{"role": "user", "content": message}],
            "request": {"mode": mode, "message": message},
            "workspace_id": workspace_id, "home": home,
        })
        res = agent.execute(
            _json.dumps({"mode": mode, "message": message, "workspace_id": workspace_id}),
            provider_ref=provider_ref, model_ref=model_ref)
        agent.dehydrate()
        duration_ms = int((_time.time() - t0) * 1000)
        return {
            "agent_id": aid, "status": "ok", "session": agent.name, "mode": mode,
            "duration_ms": duration_ms,
            "provider_ref": res.get("provider_ref") or provider_ref,
            "model_ref": res.get("model_ref") or model_ref,
            **res,
        }
    except RuntimeError as e:
        # Agent déjà hydraté (double exécution) — on ne devrait pas y arriver
        # grâce au verrou, mais par sécurité on le signale proprement.
        return {"status": "error", "error": str(e), "mode": mode, "agent_id": aid}
    except Exception as e:
        return {"status": "error", "error": str(e), "mode": mode, "agent_id": aid}
    finally:
        lock.release()


def op_dev_chat_send(params: dict) -> Dict[str, Any]:
    """Envoie un message au pilote de dev-chat (mode synchrone).

    params : { message, mode: 'plan'|'build', session?: 'nom' (défaut
    auto-généré), workspace_id? }
    """
    message = params.get("message", "")
    mode = params.get("mode", "plan")
    session = params.get("session", "") or f"devchat_{abs(hash(message)) & 0xffff}"
    if not message:
        return {"status": "error", "error": "message requis"}
    if mode not in ("plan", "build"):
        return {"status": "error", "error": f"mode invalide: {mode} (attendu plan|build)"}

    from services.agent_manager.service import AgentManager

    mgr = AgentManager()
    try:
        aid, home, workspace_id = _resolve_pilot(mgr, session, params)
    except Exception as e:
        return {"status": "error", "error": str(e)}

    try:
        result = _run_pilot(aid, mode, message, workspace_id, home,
                            params.get("provider_ref", ""),
                            params.get("model_ref", ""))
    except Exception as e:
        return {"status": "error", "error": str(e)}

    return {"status": "ok", "session": session, **result}


def _stream_writer_send(wfile, event: str, data: dict) -> None:
    try:
        wfile.write(f"event: {event}\ndata: {_json.dumps(data)}\n\n".encode())
        wfile.flush()
    except Exception:
        pass


def op_dev_chat_stream(params, wfile) -> None:
    """SSE : exécute le pilote et diffuse chaque delta (thinking/content/
    tool) du LLM en temps réel via le StreamBus.

    Flux : event: delta (kind+chunk) puis event: result (résultat final) puis
    event: done. Le run se lance en arrière-plan ; le StreamBus (in-process ou
    SQLite WAL) relie le skill autonomous au handler SSE.
    """
    from AgentFrameWork.stream_bus import stream_bus

    message = params.get("message", "")
    mode = params.get("mode", "plan")
    session = params.get("session", "") or f"devchat_{abs(hash(message)) & 0xffff}"
    if not message:
        _op_writer_send(wfile, "error", {"error": "message requis"})
        _write_done(wfile)
        return
    if mode not in ("plan", "build"):
        _op_writer_send(wfile, "error", {"error": f"mode invalide: {mode}"})
        _write_done(wfile)
        return

    from services.agent_manager.service import AgentManager

    mgr = AgentManager()
    try:
        aid, home, workspace_id = _resolve_pilot(mgr, session, params)
    except Exception as e:
        _op_writer_send(wfile, "error", {"error": str(e)})
        _write_done(wfile)
        return

    stream_bus.reset(aid)
    result: Dict[str, Any] = {}
    done_flag = threading.Event()

    def _bg_run():
        try:
            result.update(_run_pilot(aid, mode, message, workspace_id, home,
                                     params.get("provider_ref", ""),
                                     params.get("model_ref", ""),
                                     db=mgr.db))
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
        finally:
            done_flag.set()

    threading.Thread(target=_bg_run, daemon=True).start()

    seq = 0
    deadline = _time.time() + params.get("timeout", 900)
    while not done_flag.is_set() and _time.time() < deadline:
        for ev in stream_bus.since(aid, seq):
            seq = max(seq, ev["seq"])
            kind = ev.get("kind") or "content"
            _op_writer_send(wfile, "delta",
                           {"kind": kind, "chunk": ev.get("chunk", ""),
                            "seq": ev["seq"]})
        _time.sleep(0.15)
    # Drain résiduel après la fin du run
    for ev in stream_bus.since(aid, seq):
        seq = max(seq, ev["seq"])
        kind = ev.get("kind") or "content"
        _op_writer_send(wfile, "delta",
                       {"kind": kind, "chunk": ev.get("chunk", ""), "seq": ev["seq"]})
    if not done_flag.is_set():
        _op_writer_send(wfile, "error", {"error": "timeout du run"})
    else:
        _op_writer_send(wfile, "result", result or {"status": "error", "error": "aucun résultat"})
    _write_done(wfile)


def _op_writer_send(wfile, event: str, data: dict) -> None:
    _stream_writer_send(wfile, event, data)


def _write_done(wfile) -> None:
    _stream_writer_send(wfile, "done", {"done": True})


register("dev-chat/send", op_dev_chat_send)
register_streaming("dev-chat/stream", op_dev_chat_stream)