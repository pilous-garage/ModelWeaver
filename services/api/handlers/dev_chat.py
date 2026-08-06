"""Routes dev-chat — chat de développement piloté par un agent maître.

Le pilote (agent `dev-chat/chat-pilot` de la team dev-chat) a deux MODES
gérés par le FSM (variable `mode`) :
  - plan  : READ-ONLY. Explore le code, écrit `plan.md` dans son home,
            décompose en TÂCHES workspace (explore_task, coding_task,
            test_task, review_task).
  - build : orchestre l'exécution via les tâches du workspace (les membres
            gloutons explore/coder/tester/reviewer les piochent et exécutent),
            puis vérifie la complétion.

La délégation est basée sur les TÂCHES WORKSPACE (greedy), pas call_agent.
"""

from typing import Any, Dict

from services.api.router import register

# Nom du pilote dans la team dev-chat.
PILOT_AGENT = "team:dev-chat/chat-pilot"
DEFAULT_WORKSPACE = "mw-dev-chat"

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
1. Crée/lance les tâches restantes (workspace/task_create@v1, role_required
   adapté : coder_junior/coder_senior, tester, reviewer).
2. Surveille l'avancement : workspace/task_list@v1 + workspace/task_get@v1.
3. Vérifie la COMPLÉTION : une tâche doit être done avec résultat/commit ;
   sinon relance une tâche corrective.
4. Lis ton plan.md ({{home}}/plan.md) si besoin.
5. Quand tout est done, fais une synthèse et réponds.
Utilise workspace/chat_post@v1 pour communiquer avec les membres."""


def _pilot_workflow(mode: str, workspace_id: str, home: str) -> Dict[str, Any]:
    """Workflow FSM du pilote : mode plan ou build via un switch."""
    ctx = (PLAN_PROMPT if mode == "plan" else BUILD_PROMPT) \
        .replace("{{home}}", home).replace("{{workspace_id}}", workspace_id)
    step_id = "run_plan" if mode == "plan" else "run_build"
    return {
        "steps": [
            {"id": "start", "type": "call", "fn": "workflow/autonomous@v1",
             "inputs": {
                 "request": "{{request}}",
                 "bundles": ["manager", "dev"],
                 "workspace_id": workspace_id,
                 "context": ctx,
                 "max_loops": 120,
             },
             "capture": {"stdout": "result"},
             "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]
    }


def op_dev_chat_send(params: dict) -> Dict[str, Any]:
    """Envoie un message au pilote de dev-chat.

    params : { message, mode: 'plan'|'build', session?: 'nom' (defaut
    auto-généré), workspace_id? }
    Le pilote est un agent (re)cédé avec le workflow du mode. L'historique du
    chat est conservé dans variables_json.messages (comme le chat).
    """
    message = params.get("message", "")
    mode = params.get("mode", "plan")
    session = params.get("session", "") or f"devchat_{abs(hash(message)) & 0xffff}"
    workspace_id = params.get("workspace_id") or DEFAULT_WORKSPACE
    if not message:
        return {"status": "error", "error": "message requis"}
    if mode not in ("plan", "build"):
        return {"status": "error", "error": f"mode invalide: {mode} (attendu plan|build)"}

    from services.agent_manager.service import AgentManager
    from services._common import mw_home

    mgr = AgentManager()

    # Réutilise l'agent pilote de session s'il existe, sinon le crée.
    row = mgr.get_by_name(session)
    if not row or row.get("role_type") != "chat":
        created = mgr.create_chat_session(name=session, system_prompt=params.get("system_prompt", ""))
        if created.get("status") != "ok":
            return created
        aid = created["agent_id"]
    else:
        aid = row["agent_id"]

    # Home du pilote (pour plan.md).
    home = str(mw_home() / "agent_home" / str(aid))

    # Reconfigure le pilote avec le workflow du mode (plan/build).
    try:
        mgr.db.conn.execute(
            "UPDATE agents SET config_json=?, variables_json=? WHERE agent_id=?",
            (__import__("json").dumps({"workflow": _pilot_workflow(mode, workspace_id, home)}),
             __import__("json").dumps({"messages": [], "workspace_id": workspace_id,
                                       "mode": mode, "home": home}), aid))
        mgr.db.conn.commit()
    except Exception as e:
        return {"status": "error", "error": f"reconfig pilote: {e}"}

    # Exécute le pilote : le workflow autonomous tourne avec les bundles
    # manager+dev, le message est passé dans request.
    from services.api._shared import _get_llm
    try:
        import json as _json
        from services.agent_manager.service import Agent
        agent = Agent.hydrate(aid, mgr.db)
        # variables : request contient mode + message (pour {{request.mode}})
        agent._data["variables_json"] = _json.dumps({
            "messages": [{"role": "user", "content": message}],
            "request": {"mode": mode, "message": message},
            "workspace_id": workspace_id, "home": home,
        })
        res = agent.execute(
            _json.dumps({"mode": mode, "message": message, "workspace_id": workspace_id}),
            provider_ref="", model_ref="")
        agent.dehydrate()
        return {"status": "ok", "session": session, "mode": mode, **res}
    except Exception as e:
        return {"status": "error", "error": str(e)}


register("dev-chat/send", op_dev_chat_send)
