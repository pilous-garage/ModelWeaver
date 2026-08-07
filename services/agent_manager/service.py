#!/usr/bin/env python3
"""Agent Manager — Superviseur des agents.

Cycle de vie :
  1. Ticker tick() → AgentManager.tick()
  2. AgentManager vérifie les heartbeats, nettoie les zombies
  3. Les agents sont des threads hydratés/déshydratés

Usage:
    manager = AgentManager()
    manager.tick()  # appelé par le Ticker
"""

import json
import os
import signal
import threading
import time
from typing import Any, Dict, List, Optional
from pathlib import Path

from services._common import mw_home, acquire_instance_lock
from modules.sql.db import AgentsDB
from modules.sql.workspace import _compatible_roles
from modules.llm_manager.llm_manager import LLMManager
from modules.llm_manager.base_bridge import BridgeError
from AgentFrameWork.fsm_interpreter import FSMInterpreter, FSMResult, AgentAbort
from AgentFrameWork.stream_bus import stream_bus

# Intervalle de rafraîchissement du heartbeat pendant une exécution inline.
# Les agents s'exécutent INLINE (bloquant) dans le daemon ; un seul `llm_call`
# peut durer > 30s. Le Ticker de supervision (AgentManager.tick ->
# check_heartbeats, défaut 30s) tuerait l'agent à tort s'il ne voit pas de
# heartbeat frais. On rafraîchit donc le heartbeat périodiquement PENDANT
# l'exécution (voir _execute_with_fsm / chat_turn).
HEARTBEAT_INTERVAL_SECONDS = 8


def _spawn_heartbeat(agent_id: int, db) -> "threading.Event":
    """Démarre un thread de fond qui rafraîchit le heartbeat de l'agent
    toutes les HEARTBEAT_INTERVAL_SECONDS. Retourne l'Event de stop.

    Le thread utilise sa PROPRE connexion BDD (les connexions SQLite ne sont
    pas thread-safe) pour ne pas entrer en collision avec l'exécution
    principale de l'agent."""
    import threading
    from modules.sql.db import AgentsDB

    stop = threading.Event()

    def _loop():
        hb_db = AgentsDB()
        while not stop.is_set():
            try:
                hb_db.conn.execute(
                    "UPDATE agent_runtime SET heartbeat_at = datetime('now') "
                    "WHERE agent_id = ?", (agent_id,))
                hb_db.conn.commit()
            except Exception:
                pass
            stop.wait(HEARTBEAT_INTERVAL_SECONDS)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return stop


# ── Bridge LLM partagé : branché sur le Key Manager (clés en BDD) ──
# Les agents s'exécutent via le daemon, dont l'environnement ne porte pas les
# clés API (ex: GROQ_API_KEY). On résout donc les clés via le KeyManager
# (stockées en BDD, hors env) et les endpoints/api_type via le catalogue.
_cat_db = None
_km = None


def _get_catalogue_db():
    global _cat_db
    if _cat_db is None:
        from modules.sql.db import CatalogueDB
        _cat_db = CatalogueDB()
    return _cat_db


def _get_key_manager():
    global _km
    if _km is None:
        from modules.key_manager.key_manager import KeyManager
        from modules.sql.db import ModelWeaverDB
        _km = KeyManager(ModelWeaverDB())
    return _km


def make_bridge():
    """Construit le bridge actif via LLMManager (DirectBridge par défaut)."""
    return LLMManager(cat=_get_catalogue_db(), km=_get_key_manager()).get_bridge()


# Workflow minimal d'un tour de chat : un seul `llm_call` qui consomme le
# historique complet passé à run() (messages), diffuse via StreamBus, et
# ajoute la réponse à l'historique. Le chat est un agent `role_type='chat'`
# exécuté via ce workflow — aucune logique LLM dupliquée hors du framework.
CHAT_WORKFLOW = {
    "steps": [
        {"id": "chat", "type": "llm_call",
         "provider_ref": "", "model_ref": "",
         "temperature": 0.7, "max_tokens": 4096,
         "output_capture": "_reply", "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]
}


# ──────────────────────────────────────────────
#  Agent — Runtime wrapper (Phase 1 minimal)
# ──────────────────────────────────────────────

# ── Constantes ────────────────────────────────────────────────────────────────

TICK_INTERVAL = 1          # secondes entre chaque cycle de supervision
MAX_THREAD_AGENTS = 100    # agents actifs simultanés max
MAX_SLEEPING_AGENTS = 10000  # agents endormis max dans la BDD
MIN_DISK_FREE_GB = 1       # espace disque libre minimum avant de créer un agent
MAX_TOTAL_AGENT_DISK_GB = 10  # espace disque total max utilisé par tous les agents

# Mapping role_type d'agent → rôle requis par une tâche (workspace greedy).
# Une tâche avec role_required='coder_senior' réveille les agents 'codeur'.
# Pour l'instant TOUS les rôles sont "senior" (pas de junior) : on ajustera
# quand on connaîtra mieux le catalogue (les juniors prendraient les modèles
# peu coûteux comme gemma-4, qui bouclent sur les tâches complexes).
ROLE_TO_TASK = {
    "architecte": "analyst",
    "planificateur": "analyst",
    "explorateur": "explore",
    "explore": "explore",
    "codeur": "coder_senior",
    "test_runner": "tester",
    "relecteur": "reviewer",
    "orchestrateur": "merger",
}


class Agent:
    """Wrapper runtime d'un agent hydraté.

    L'agent existe en BDD (table agents). Cette classe est une enveloppe
    pour interagir avec lui pendant son cycle d'exécution.
    """

    def __init__(self, db: AgentsDB, agent_data: Dict[str, Any]):
        self.db = db
        self._data = agent_data
        self._bridge = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self.agent_id = agent_data["agent_id"]
        self.name = agent_data["name"]
        self.ref = agent_data["ref"]
        self.role_type = agent_data["role_type"]
        self.occupation = agent_data["occupation"]
        self.status = agent_data["status"]
        self._call_provider_ref = ""
        self._call_model_ref = ""

        # Lifecycle hooks
        config = json.loads(self._data.get("config_json") or "{}")
        from services.lifecycle import LifecycleManager
        self._lifecycle = LifecycleManager(self.agent_id, config)

    @classmethod
    def hydrate(cls, agent_id: int, db: Optional[AgentsDB] = None) -> "Agent":
        """Hydrate un agent depuis la BDD.

        Charge les données, vérifie l'existence, crée une entrée runtime
        et initialise le shell interne de l'agent.
        """
        db = db or AgentsDB()
        row = db.conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Agent {agent_id} introuvable")

        self = cls(db, dict(row))

        # Marquer comme IDLE dans la BDD
        db.conn.execute(
            "UPDATE agents SET status = 'IDLE', last_active_at = datetime('now') "
            "WHERE agent_id = ?", (agent_id,)
        )

        # Créer l'entrée runtime (thread actif)
        thread_id = f"agent:{self.name}:{int(time.time())}"
        db.conn.execute("""
            INSERT OR REPLACE INTO agent_runtime
                (agent_id, thread_id, pid, heartbeat_at, started_at, current_step)
            VALUES (?, ?, ?, datetime('now'), datetime('now'), 'hydrated')
        """, (agent_id, thread_id, os.getpid()))
        db.conn.commit()

        # Initialiser le shell interne de l'agent
        try:
            from services.agent_shell_manager import agent_shell_manager
            agent_shell_manager.init()
            config = json.loads(self._data.get("config_json") or "{}")
            role = config.get("role", "member")
            from services._common import mw_home
            home_root = (mw_home() / "agent_home" / self.name).resolve()
            agent_shell_manager.get_or_create(
                agent_id=self.name,
                role=role,
                home_root=home_root,
            )
        except Exception as e:
            # Le shell est optionnel — ne pas bloquer l'hydratation
            pass

        return self

    def execute(self, request: str, provider_ref: str = "", model_ref: str = "",
                 temperature: float = 0.7, max_tokens: int = 4096,
                 entrypoint: str = "main") -> Dict[str, Any]:
        """Exécute une requête via le Bridge LLM ou le FSM Interpreter.

        Si l'agent a un workflow défini (config.workflow, config.entrypoints,
        ou role.pipeline), le FSM Interpreter est utilisé. Sinon, appel
        Bridge direct (Phase 1).
        Phase 4 : consomme les signaux (pause/kill/configure/...) et diffuse
        les chunks via le StreamBus.
        """
        self._mark_running()
        stream_bus.reset(self.agent_id)

        # Logger FSM : trace chaque step/appel LLM/tool dans {home}/log/.
        # Home par AGENT_ID (agent_home/{id}) — le même que celui utilisé par
        # les skills (autonomous.py) — pour que steps FSM + appels LLM/tools
        # atterrissent dans UN SEUL fichier par run.
        # Niveau depuis la config de l'agent (défaut debug).
        self._fsm_log = None
        try:
            from AgentFrameWork.fsm_logger import FSMLogger
            from services._common import mw_home
            home_root = (mw_home() / "agent_home" / str(self.agent_id)).resolve()
            cfg0 = json.loads(self._data.get("config_json") or "{}")
            lvl = (cfg0.get("log_level")
                   or (cfg0.get("llm") or {}).get("log_level")
                   or "debug")
            self._fsm_log = FSMLogger(str(home_root),
                                      agent_name=self.name, level=lvl)
            self._fsm_log.log("info", "fsm/start",
                              f"agent={self.name} request={request[:80]}")
        except Exception:
            self._fsm_log = None

        # Charger le bridge si pas déjà fait
        if not self._bridge:
            self._bridge = make_bridge()

        # Résoudre le workflow (FSM) — priorité aux entrypoints
        config = json.loads(self._data.get("config_json") or "{}")
        if "entrypoints" in config and entrypoint in config["entrypoints"]:
            workflow = config["entrypoints"][entrypoint]
        elif entrypoint == "main":
            workflow = config.get("workflow") or config.get("pipeline")
        else:
            workflow = None
        if workflow and isinstance(workflow, dict):
            from services.skill_manager import expand_workflow
            try:
                workflow = expand_workflow(workflow)
            except Exception:
                pass

        # Construire les messages initiaux
        messages = self._build_messages(request, config)

        # Contrôleur de signaux + diffusion (partagés FSM / Phase 1)
        signal_check = self._make_signal_check()
        stream_sink = lambda chunk: stream_bus.publish(self.agent_id, chunk, "token")
        # Phase 5 : orchestration (spawn d'enfants, handoff de session)
        # Phase 5b : agent_call (appel synchrone d'un entrypoint d'un autre agent)
        spawn_handler = self._make_spawn_handler()
        handoff_handler = self._make_handoff_handler()
        agent_call_handler = self._make_agent_call_handler()

        # Stocker provider/model pour que agent_call_handler les propage
        self._call_provider_ref = provider_ref
        self._call_model_ref = model_ref
        try:
            if workflow and len(workflow.get("steps", [])) > 0:
                # Phase 2 : FSM Interpreter
                result = self._execute_with_fsm(
                    workflow, messages,
                    provider_ref=provider_ref,
                    model_ref=model_ref,
                    signal_check=signal_check,
                    stream_sink=stream_sink,
                    spawn_handler=spawn_handler,
                    handoff_handler=handoff_handler,
                    agent_call_handler=agent_call_handler,
                    lifecycle_mgr=self._lifecycle,
                )
                result = result.to_dict()
            else:
                # Phase 1 : appel Bridge direct (signaux vérifiés avant appel)
                _hb_stop = _spawn_heartbeat(self.agent_id, self.db)
                try:
                    signal_check(FSMResult())  # consomme pause/kill/configure en attente
                    t0 = time.time()
                    result = self._bridge.chat(
                        provider_ref=provider_ref,
                        model_ref=model_ref,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        agent_id=str(self.agent_id),
                    )
                    elapsed_ms = int((time.time() - t0) * 1000)
                    content = result.content if hasattr(result, 'content') else str(result)
                    tokens = 0
                    if hasattr(result, 'usage') and isinstance(result.usage, dict):
                        tokens = result.usage.get("total_tokens", 0)
                    budget = getattr(result, 'budget', {}) or {}
                    stream_sink(content)
                    result = {"status": "ok", "content": content, "tokens_used": tokens,
                              "budget": budget, "elapsed_ms": elapsed_ms}
                finally:
                    _hb_stop.set()

            # Enregistrer les métriques
            db = self.db
            tokens = result.get("tokens_used", 0)
            elapsed_ms = result.get("elapsed_ms", 0)
            success = result.get("status") in ("ok", "success")
            db.conn.execute("""
                INSERT INTO agent_metrics (agent_id, total_tasks, total_tokens,
                                            failed_tasks, total_runtime_ms, avg_latency_ms)
                VALUES (?, 1, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    total_tasks = total_tasks + 1,
                    total_tokens = total_tokens + ?,
                    failed_tasks = failed_tasks + ?,
                    total_runtime_ms = total_runtime_ms + ?,
                    avg_latency_ms = (total_runtime_ms + ?) * 1.0 / (total_tasks + 1),
                    last_updated = datetime('now')
            """, (self.agent_id, tokens, 0 if success else 1, elapsed_ms, elapsed_ms,
                  tokens, 0 if success else 1, elapsed_ms, elapsed_ms))
            db.conn.commit()

            return result

        except AgentAbort:
            self._record_failure()
            return {"status": "aborted", "error": "Interrompu par signal kill"}

        except BridgeError as e:
            self._record_failure()
            return {"status": "error", "error": str(e)}

        except Exception as e:
            self._record_failure()
            return {"status": "error", "error": str(e)}

        finally:
            self._mark_idle()

    def chat_turn(
        self, user_message: str,
        provider_ref: str = "", model_ref: str = "",
        temperature: float = 0.7, max_tokens: int = 4096,
        system_prompt: str = "",
    ) -> Dict[str, Any]:
        """Tour de chat multi-turn via le framework (FSM + StreamBus + signaux).

        Le chat est un agent : on recharge l'historique depuis variables_json,
        on exécute CHAT_WORKFLOW avec l'historique + le nouveau message, et on
        persiste l'historique enrichi. Aucune logique LLM propre à ChatService.
        """
        self._mark_running()
        stream_bus.reset(self.agent_id)

        if not self._bridge:
            self._bridge = make_bridge()

        variables = json.loads(self._data.get("variables_json") or "{}")
        history = variables.get("messages", [])

        # Reconstruire les messages LLM : system + historique + nouveau user
        llm_messages: List[Dict[str, str]] = []
        system = system_prompt or variables.get("system_prompt", "")
        if system:
            llm_messages.append({"role": "system", "content": system})
        llm_messages.extend(history)
        llm_messages.append({"role": "user", "content": user_message})

        signal_check = self._make_signal_check()
        # Streaming non disponible sur DirectBridge (chat_stream non implémenté) :
        # mode NON-stream (le FSM appelle bridge.chat()). Le stream_bus reste
        # alimenté à la fin pour compat agent/stream.
        stream_sink = None
        _hb_stop = _spawn_heartbeat(self.agent_id, self.db)

        try:
            workflow = {
                "steps": [
                    {"id": "chat", "type": "llm_call",
                     "provider_ref": "", "model_ref": "",
                     "temperature": temperature, "max_tokens": max_tokens or 4096,
                     "output_capture": "_reply", "next": "end"},
                    {"id": "end", "type": "end", "status": "SUCCESS"},
                ]
            }
            fsm = FSMInterpreter(bridge=self._bridge, tool_executor=None)
            result: FSMResult = fsm.run(
                workflow=workflow,
                messages=llm_messages,
                variables=variables,
                provider_ref=provider_ref,
                model_ref=model_ref,
                signal_check=signal_check,
                stream_sink=stream_sink,
            )
            if result.status not in ("ok", "success", "running"):
                return {"status": "failed",
                        "error": result.end_reason or "échec FSM"}

            # Retirer le system prompt qu'on avait ajouté : l'historique ne
            # stocke que les tours user/assistant.
            new_history = result.messages[1:] if system else list(result.messages)
            # NB : result.variables est une COPIE de `variables` prise au démarrage
            # de run() (donc avec messages=[]). On fusionne d'abord les variables
            # du FSM, PUIS on (ré)écrit l'historique courant pour ne pas l'écraser.
            for k, v in result.variables.items():
                variables[k] = v
            variables["messages"] = new_history
            self.db.conn.execute(
                "UPDATE agents SET variables_json = ? WHERE agent_id = ?",
                (json.dumps(variables), self.agent_id))
            self.db.conn.commit()

            return {"status": "ok", "reply": result.content,
                    "content": result.content,
                    "messages": new_history,
                    "tokens_used": result.tokens_used,
                    "budget": result.budget,
                    "provider_ref": result.variables.get("_llm_provider", provider_ref),
                    "model_ref": result.variables.get("_llm_model", model_ref)}
        except AgentAbort:
            self._record_failure()
            return {"status": "aborted", "error": "Interrompu par signal kill"}
        except BridgeError as e:
            self._record_failure()
            return {"status": "error", "error": str(e), "category": "llm"}
        except Exception as e:
            self._record_failure()
            return {"status": "error", "error": str(e)}
        finally:
            _hb_stop.set()
            self._mark_idle()

    def _make_signal_check(self):
        """Retourne un callable (result) -> None qui consomme les signaux
        PENDING de cet agent et applique leurs effets (Phase 4)."""
        agent_id = self.agent_id
        db = self.db

        def _check(result: "FSMResult") -> None:
            mgr = AgentManager(db=db)
            for sig in mgr.pending_signals(agent_id):
                stype = sig["type"]
                try:
                    payload = json.loads(sig["payload_json"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                # Lifecycle on_signal hook
                try:
                    if hasattr(self, '_lifecycle'):
                        self._lifecycle.publish("on_signal",
                            signal_type=stype, signal_payload=payload,
                            variables=dict(getattr(result, 'variables', {})))
                except Exception:
                    pass
                if stype == "kill":
                    mgr.ack_signal(sig["signal_id"])
                    mgr.complete_signal(sig["signal_id"], {"action": "kill"})
                    raise AgentAbort()
                elif stype == "pause":
                    result._paused = True
                    mgr.ack_signal(sig["signal_id"])
                    mgr.complete_signal(sig["signal_id"], {"action": "pause"})
                elif stype == "resume":
                    result._paused = False
                    mgr.ack_signal(sig["signal_id"])
                    mgr.complete_signal(sig["signal_id"], {"action": "resume"})
                elif stype == "configure":
                    result.variables.update(payload.get("variables", {}))
                    if "state" in payload:
                        db.conn.execute(
                            "UPDATE agents SET state_json = ? WHERE agent_id = ?",
                            (json.dumps(payload["state"]), agent_id))
                        db.conn.commit()
                    mgr.ack_signal(sig["signal_id"])
                    mgr.complete_signal(sig["signal_id"],
                                        {"action": "configure",
                                         "applied": payload.get("variables", {})})
                else:  # status / health
                    mgr.ack_signal(sig["signal_id"])
                    mgr.complete_signal(sig["signal_id"],
                                        {"action": stype, "heartbeat": "ok"})

        return _check

    def _build_messages(self, request: str, config: Dict) -> List[Dict[str, str]]:
        """Construit les messages initiaux (system prompt + request)."""
        messages = []
        personality = config.get("personality", {})
        tone = personality.get("tone", "")
        if tone:
            messages.append({"role": "system", "content": f"Ton : {tone}"})
        messages.append({"role": "user", "content": request})
        return messages

    def _execute_with_fsm(
        self, workflow: Dict, messages: List[Dict],
        provider_ref: str = "", model_ref: str = "",
        signal_check: Any = None, stream_sink: Any = None,
        spawn_handler: Any = None, handoff_handler: Any = None,
        agent_call_handler: Any = None,
        lifecycle_mgr: Any = None,
    ) -> "FSMResult":
        """Exécution via FSM Interpreter (Phase 4 : signaux + streaming,
        Phase 5 : spawn + handoff)."""
        import json
        variables = json.loads(self._data.get("variables_json") or "{}")
        # Purger les variables de capture du run précédent (_member_*, sorties
        # d'agent_call) : sans quoi un nouveau run réaffiche les anciennes
        # sorties des membres dans ses variables finales.
        for _k in [k for k in variables if k.startswith("_member_")]:
            del variables[_k]
        # Injecte agent_id pour les skills (memory, log) via {{agent_id}}.
        variables.setdefault("agent_id", self.agent_id)
        # Injecte la requête utilisateur (dernier message user) via {{request}}.
        # Toujours écrasée (et non setdefault) : un agent multi-phases doit voir
        # la requête courante, pas celle persistée d'une exécution précédente.
        for _m in reversed(messages):
            if _m.get("role") == "user":
                variables["request"] = _m.get("content", "")
                break
        # Suivi live du step courant : la GUI (agent/list) lit
        # agent_runtime.current_step. Le FSM émet `post_step` à chaque étape ;
        # on le reflète en BDD pour que l'activité soit observable en direct
        # (sinon la GUI ne voit que le littéral 'running' pendant toute l'exéc).
        from services.lifecycle import get_event_bus, HookType
        _bus = get_event_bus()
        _agent_id = self.agent_id
        _db = self.db

        def _on_post_step(event):
            if event.agent_id != _agent_id:
                return
            try:
                _db.conn.execute(
                    "UPDATE agent_runtime SET current_step = ?, "
                    "heartbeat_at = datetime('now') WHERE agent_id = ?",
                    (event.step_id or "running", _agent_id))
                _db.conn.commit()
            except Exception:
                pass
            # Log FSM : chaque step exécuté, avec type + args courts.
            try:
                _l = getattr(self, "_fsm_log", None)
                if _l is not None:
                    _st = event.step or {}
                    _args = {k: v for k, v in (_st.get("inputs") or {}).items()
                             if not isinstance(v, (dict, list))}
                    _l.log("debug", "fsm/step",
                           f"id={event.step_id or '?'} type={_st.get('type', '?')} "
                           f"args={_args}")
            except Exception:
                pass

        _bus.subscribe(HookType.POST_STEP, _on_post_step)
        # Rafraîchir le heartbeat en arrière-plan pendant toute l'exécution
        # (sinon un llm_call > 30s fait tuer l'agent par le Ticker).
        _hb_stop = _spawn_heartbeat(_agent_id, _db)
        try:
            fsm = FSMInterpreter(
                bridge=self._bridge,
                tool_executor=None,  # tools gérés en Phase 3
            )
            result: FSMResult = fsm.run(
                workflow=workflow,
                messages=messages,
                variables=variables,
                provider_ref=provider_ref,
                model_ref=model_ref,
                signal_check=signal_check,
                stream_sink=stream_sink,
                spawn_handler=spawn_handler,
                handoff_handler=handoff_handler,
                agent_call_handler=agent_call_handler,
                lifecycle_mgr=lifecycle_mgr,
            )
            # Persister les variables (survit à configure / spawn / handoff)
            state = {"current_step": result.next_step_id}
            # Occupation `disparate` : l'agent retourne dormir en BDD après exécution
            if self.occupation == "disparate":
                state["sleeping"] = True
            self.db.conn.execute(
                "UPDATE agents SET variables_json = ?, state_json = ? WHERE agent_id = ?",
                (json.dumps(result.variables), json.dumps(state), self.agent_id),
            )
            self.db.conn.commit()
            return result
        finally:
            _hb_stop.set()
            _bus.unsubscribe(HookType.POST_STEP, _on_post_step)

    def _make_spawn_handler(self):
        """Closure : crée/exécute/endort un agent enfant via l'AgentManager."""
        db = self.db

        def _spawn(spec: Dict[str, Any], request: str) -> Dict[str, Any]:
            mgr = AgentManager(db=db)
            return mgr.spawn_agent(
                name=spec.get("name", f"child_{self.agent_id}"),
                role=spec.get("role", "spawned"),
                request=request,
                occupation=spec.get("occupation", "disparate"),
                resources=spec.get("resources"),
                config=spec.get("config"),
                provider_ref=spec.get("provider_ref", ""),
                model_ref=spec.get("model_ref", ""),
            )
        return _spawn

    def _make_handoff_handler(self):
        """Closure : transfert de session depuis cet agent vers un successeur."""
        db = self.db
        from_id = self.agent_id

        def _handoff(to: Any) -> Dict[str, Any]:
            mgr = AgentManager(db=db)
            if isinstance(to, int) or (isinstance(to, str) and to.isdigit()):
                to_id = int(to)
            else:
                row = db.conn.execute(
                    "SELECT agent_id FROM agents WHERE name = ?", (str(to),)
                ).fetchone()
                if not row:
                    raise ValueError(f"agent cible introuvable: {to}")
                to_id = row["agent_id"]
            return mgr.handoff(from_id, to_id)
        return _handoff

    def _make_agent_call_handler(self):
        """Closure : résout un agent par son nom, exécute un entrypoint
        et retourne le résultat. Utilisé par le step `agent_call`."""
        db = self.db
        parent_bridge = self._bridge

        def _call(agent_name: str, entrypoint: str, inputs: dict) -> Dict[str, Any]:
            from services.api.afd_client import get_afd_client
            import json as _json
            client = get_afd_client()
            # Le membre attend la VRAIE requête (pas le JSON de transport) :
            # les inputs du step contiennent `request` + paramètres auxiliaires.
            request = inputs.get("request", _json.dumps(inputs))
            return client.call(
                agent_name, "execute",
                request=request,
                entrypoint=entrypoint,
                provider_ref=inputs.get("provider_ref", self._call_provider_ref),
                model_ref=inputs.get("model_ref", self._call_model_ref),
            )
        return _call

    def _record_failure(self):
        """Enregistre une tâche échouée dans les métriques."""
        try:
            self.db.conn.execute("""
                INSERT INTO agent_metrics (agent_id, total_tasks, failed_tasks)
                VALUES (?, 1, 1)
                ON CONFLICT(agent_id) DO UPDATE SET
                    total_tasks = total_tasks + 1,
                    failed_tasks = failed_tasks + 1,
                    last_updated = datetime('now')
            """, (self.agent_id,))
            self.db.conn.commit()
        except Exception:
            pass

    def dehydrate(self) -> None:
        """Déshydrate l'agent : ferme le shell, sauve état, supprime runtime."""
        db = self.db

        # Libérer les claims de modèles LLM de cet agent (anti-affinité) :
        # quand l'agent se rendort, ses modèles redeviennent allouables aux
        # autres. Best-effort (TTL du service en secours).
        try:
            from services.llm_manager.client import get_llm_client
            get_llm_client().report_end(str(self.agent_id))
        except Exception:
            pass

        # Fermer le shell interne si présent
        try:
            from services.agent_shell_manager import agent_shell_manager
            agent_shell_manager.close(self.name)
        except Exception:
            pass

        # Sauvegarder l'état
        db.conn.execute("""
            UPDATE agents
            SET status = 'INIT', last_active_at = datetime('now')
            WHERE agent_id = ?
        """, (self.agent_id,))

        # Supprimer l'entrée runtime
        db.conn.execute(
            "DELETE FROM agent_runtime WHERE agent_id = ?",
            (self.agent_id,)
        )
        db.conn.commit()

        # Désabonne les hooks de cycle de vie (évite les fuites EventBus).
        if hasattr(self, "_lifecycle"):
            try:
                self._lifecycle.cleanup()
            except Exception:
                pass

    # ── Accès au shell interne ────────────────────────────────────

    @property
    def shell(self):
        """Retourne l'AgentShell de l'agent, ou None si pas de shell."""
        try:
            from services.agent_shell_manager import agent_shell_manager
            return agent_shell_manager.get(self.name)
        except Exception:
            return None

    def run_shell(self, cmd: str) -> dict:
        """Exécute une commande shell dans le contexte de l'agent.

        Retourne {"exit_code": int, "stdout": str, "stderr": str, "status": str}.
        """
        ag_sh = self.shell
        if ag_sh is None:
            return {"exit_code": 1, "stdout": "", "stderr": "shell non disponible", "status": "error"}
        return ag_sh.run(cmd)

    def get_status(self) -> Dict[str, Any]:
        """Retourne l'état courant de l'agent."""
        row = self.db.conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (self.agent_id,)
        ).fetchone()
        rt = self.db.conn.execute(
            "SELECT * FROM agent_runtime WHERE agent_id = ?", (self.agent_id,)
        ).fetchone()

        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role_type,
            "occupation": self.occupation,
            "status": row["status"] if row else "UNKNOWN",
            "hydrated": rt is not None,
            "heartbeat": rt["heartbeat_at"] if rt else None,
            "current_step": rt["current_step"] if rt else None,
        }

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._data)

    # ── Interne ──

    def _mark_running(self):
        self.db.conn.execute(
            "UPDATE agents SET status = 'RUNNING' WHERE agent_id = ?",
            (self.agent_id,)
        )
        self._update_heartbeat("running")
        self.db.conn.commit()

    def _mark_idle(self):
        self.db.conn.execute(
            "UPDATE agents SET status = 'IDLE' WHERE agent_id = ?",
            (self.agent_id,)
        )
        self._update_heartbeat("idle")
        self.db.conn.commit()

    def _update_heartbeat(self, step: str = ""):
        self.db.conn.execute("""
            UPDATE agent_runtime
            SET heartbeat_at = datetime('now'),
                current_step = ?
            WHERE agent_id = ?
        """, (step, self.agent_id))


# ──────────────────────────────────────────────
#  AgentManager — Superviseur
# ──────────────────────────────────────────────

class AgentManager:
    """Superviseur des agents.

    Vérifie les heartbeats, nettoie les zombies, expose les agents actifs.
    Ne fait PAS d'orchestration — seulement de la supervision.
    """

    def __init__(self, db: Optional[AgentsDB] = None):
        self.db = db or AgentsDB()
        self._bridge = make_bridge()

    # ── API publique ──

    def list_active(self) -> List[Dict[str, Any]]:
        """Liste les agents actuellement hydratés (thread en vie)."""
        rows = self.db.conn.execute("""
            SELECT a.agent_id, a.name, a.ref, a.role_type, a.occupation,
                   a.status, a.last_active_at,
                   r.thread_id, r.heartbeat_at, r.current_step,
                   r.pid, r.started_at
            FROM agents a
            JOIN agent_runtime r ON r.agent_id = a.agent_id
            ORDER BY a.name
        """).fetchall()
        return [dict(r) for r in rows]

    def get_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        row = self.db.conn.execute(
            "SELECT * FROM agents WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def get_by_id(self, agent_id: int) -> Optional[Dict[str, Any]]:
        row = self.db.conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        return dict(row) if row else None

    def check_heartbeats(self, max_age_seconds: int = 30) -> List[int]:
        """Retourne les IDs des agents sans heartbeat récent (zombies).

        Un agent en RUNNING doit envoyer un heartbeat toutes les
        max_age_secondes. Si dépassé, il est présumé mort.
        """
        rows = self.db.conn.execute("""
            SELECT a.agent_id FROM agents a
            JOIN agent_runtime r ON r.agent_id = a.agent_id
            WHERE r.heartbeat_at < datetime('now', ?)
        """, (f"-{max_age_seconds} seconds",)).fetchall()
        return [r["agent_id"] for r in rows]

    def kill(self, agent_id: int) -> Dict[str, Any]:
        """Tue un agent. 3 niveaux : stop → kill → pkill.

        Retourne le niveau atteint et le statut final.

        NB : dans l'architecture actuelle les agents s'exécutent INLINE dans le
        processus du daemon (pid enregistré = os.getpid()). On ne doit SURTOUT
        PAS envoyer SIGTERM/SIGKILL au daemon lui-même ; on enfile alors un
        signal `kill` que la FSM honorera (AgentAbort) au prochain
        `signal_check`. Pour les agents réellement isolés dans un processus
        séparé (architecture prévue), on applique stop → kill → pkill.
        """
        rt = self.db.conn.execute(
            "SELECT * FROM agent_runtime WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not rt:
            return {"level": 0, "status": "not_hydrated"}

        pid = rt["pid"]
        level = 0

        if pid == os.getpid():
            # Agent inline dans ce daemon : on ne peut pas le tuer par signal OS
            # sans tuer le daemon. On délègue à la FSM via le canal de signaux.
            self.send_signal(agent_id, "kill")
            level = -1  # signal-only (la FSM fera l'AgentAbort)
        else:
            # Niveau 1 : signal stop (SIGTERM)
            try:
                os.kill(pid, signal.SIGTERM)
                level = 1
                time.sleep(0.5)  # timeout court pour Phase 1
            except (OSError, ProcessLookupError):
                pass

            # Vérifier si le processus existe encore
            if self._pid_exists(pid):
                # Niveau 2 : kill (SIGKILL)
                try:
                    os.kill(pid, signal.SIGKILL)
                    level = 2
                except (OSError, ProcessLookupError):
                    pass

        # Nettoyage BDD
        self.db.conn.execute("""
            UPDATE agents SET status = 'STOPPED', last_active_at = datetime('now')
            WHERE agent_id = ?
        """, (agent_id,))
        self.db.conn.execute(
            "DELETE FROM agent_runtime WHERE agent_id = ?",
            (agent_id,)
        )
        self.db.conn.commit()

        return {"level": level, "status": "killed", "pid": pid}

    def tick(self) -> Dict[str, Any]:
        """Cycle de supervision unique. Appelé par le Ticker."""
        zombies = self.check_heartbeats()
        killed = []
        for zid in zombies:
            result = self.kill(zid)
            killed.append({"agent_id": zid, "result": result})

        # Tasks 'running' orphelines : si PLUS AUCUN agent n'est hydraté (ex.
        # redémarrage d'agent-manager → threads daemon morts), les tasks en
        # cours n'ont plus personne pour les finir. On les remet 'pending'
        # pour qu'un agent les reprenne au prochain réveil.
        reclaimed = self._reclaim_stale_tasks()
        # Issues dont le workspace d'analyse est 100% done → marquer 'done'.
        issues_completed = self._complete_done_issues()

        # Réveiller les agents endormis qui ont des signaux en attente
        woken = self._wake_sleeping_agents()
        # Réveiller les agents quand des tâches workspace sont dispo (greedy)
        woken_tasks = self._wake_for_tasks()

        active = len(self.list_active())

        return {
            "status": "ok",
            "active_agents": active,
            "zombies_found": len(zombies),
            "zombies_killed": killed,
            "woken_agents": woken,
            "woken_tasks": woken_tasks,
            "tasks_reclaimed": reclaimed,
            "issues_completed": issues_completed,
        }

    def _complete_done_issues(self) -> int:
        """Marque 'done' les issues dont le workspace d'analyse est terminé.

        L'analyste lie l'issue à son workspace de découpage
        (issue.analysis_workspace_id). Quand toutes les tasks de ce workspace
        sont 'done', l'issue est considérée terminée.
        """
        try:
            from modules.sql.workspace import WorkspaceDB
            wdb = WorkspaceDB()
            # workspaces 100% done
            done_ws = {
                w["workspace_id"]
                for w in wdb.conn.execute(
                    "SELECT workspace_id FROM tasks GROUP BY workspace_id "
                    "HAVING COUNT(*) > 0 "
                    "AND SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) = COUNT(*)"
                ).fetchall()}
            if not done_ws:
                wdb.close()
                return 0
            cur = wdb.conn.execute(
                "UPDATE issues SET status = 'done', updated_at = datetime('now') "
                "WHERE status IN ('analyzed','analysing') "
                "AND analysis_workspace_id IN (%s)"
                % ",".join("?" for _ in done_ws),
                tuple(done_ws))
            wdb.conn.commit()
            n = cur.rowcount
            wdb.close()
            return n or 0
        except Exception:
            return 0

    def _agent_team_id(self, agent_id: int) -> int:
        """team_id stable d'un agent = MIN(agent_id) de sa team (ou -1)."""
        try:
            row = self.db.conn.execute(
                "SELECT name FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if not row or not row["name"].startswith("team:"):
                return -1
            team = row["name"].split("/")[0]
            r = self.db.conn.execute(
                "SELECT MIN(agent_id) AS mid FROM agents WHERE name LIKE ?",
                (team + "/%",)).fetchone()
            return r["mid"] if r and r["mid"] else -1
        except Exception:
            return -1

    def _has_unpushed(self, team_id: int) -> bool:
        """Vrai si la branche auto_code_<team_id> du repo central a des commits
        non encore poussés vers github (origin).

        Utilisé par le waker : si tout est déjà poussé, réveiller l'intégrateur
        ne sert à rien (il boucle : push vide → re-register → réveil).
        """
        try:
            from services._common import mw_home
            bare = mw_home() / "repos" / "mw-swarm.git"
            if not bare.exists():
                return False
            branch = f"auto_code_{team_id}"
            import subprocess as sp
            def _rev(ref):
                r = sp.run(["git", "--git-dir", str(bare), "rev-parse", "-q",
                            "--verify", ref], capture_output=True, text=True,
                           timeout=15)
                return r.stdout.strip() if r.returncode == 0 else ""
            local = _rev(f"refs/heads/{branch}")
            remote = _rev(f"refs/remotes/origin/{branch}")
            if not local:
                return False
            if not remote:
                return True  # branche locale sans distante → à pousser
            return local != remote
        except Exception:
            return False

    def _reclaim_stale_tasks(self) -> int:
        """Remet les tasks 'running' à 'pending' si plus aucun agent n'est en
        cours d'exécution légitime.

        Un agent "légitimement actif" = présent dans agent_runtime ET de statut
        RUNNING/IDLE (les reliquats de runs morts ont un statut INIT/None après
        redémarrage). Si aucun agent ne tourne, toutes les tasks 'running' sont
        orphelines → on les libère pour qu'un agent les reprenne.
        """
        try:
            n_active = self.db.conn.execute("""
                SELECT COUNT(*) FROM agent_runtime r
                JOIN agents a ON a.agent_id = r.agent_id
                WHERE a.status IN ('RUNNING', 'IDLE')
            """).fetchone()[0]
            if n_active > 0:
                return 0
            from modules.sql.workspace import WorkspaceDB
            wdb = WorkspaceDB()
            n = wdb.conn.execute(
                "UPDATE tasks SET status = 'pending', updated_at = datetime('now') "
                "WHERE status = 'running'").rowcount
            wdb.conn.commit()
            wdb.close()
            return n or 0
        except Exception:
            return 0

    def _wake_sleeping_agents(self) -> int:
        """Hydrate et exécute les agents endormis qui ont des signaux PENDING."""
        rows = self.db.conn.execute("""
            SELECT DISTINCT a.agent_id, a.name, a.role_type
            FROM agents a
            JOIN agent_signals s ON s.agent_id = a.agent_id
            WHERE s.status = 'PENDING'
              AND a.agent_id NOT IN (SELECT agent_id FROM agent_runtime)
        """).fetchall()

        active = len(self.list_active())
        count = 0
        for row in rows:
            if active + count >= MAX_THREAD_AGENTS:
                break
            agent_id = row["agent_id"]
            threading.Thread(target=self._run_sleeping_agent, args=(agent_id,), daemon=True).start()
            count += 1
        return count

    def _run_sleeping_agent(self, agent_id: int, wakeup_request: str = "wakeup: signals pending",
                            workspace_id: str = "") -> None:
        agent = None
        try:
            agent = Agent.hydrate(agent_id, db=self.db)
            if workspace_id:
                # Injecte le workspace + le rôle greedy + team_id dans les
                # variables : l'agent sait où piocher, avec quel rôle, et dans
                # quelle team. team_id = id du plus ancien membre de la team
                # (stable par team) ; -1 si pas de team.
                try:
                    import json as _json
                    vars_j = _json.loads(agent._data.get("variables_json") or "{}")
                    vars_j["workspace_id"] = workspace_id
                    # project_id git = le workspace de la TEAM (repo central de
                    # référence, ex. mw-swarm), pas le workspace des tâches.
                    name = agent._data.get("name", "")
                    try:
                        # 0) Source de vérité : le manifest de la team
                        # (workspace_id) — évite tout devinement fragile.
                        from services.team_spec import TeamSpec
                        _team_name = name.split("/")[0][len("team:"):] if name.startswith("team:") else ""
                        _proj = ""
                        if _team_name:
                            try:
                                _spec = TeamSpec.from_yaml(
                                    f"services/manifests/teams/{_team_name}.team.yaml")
                                _proj = _spec.workspace_id or ""
                            except Exception:
                                _proj = ""
                        if _proj:
                            vars_j["project_id"] = _proj
                        else:
                            # fallback : workspace lié à la team (director)
                            from modules.sql.workspace import WorkspaceDB
                            _wdb = WorkspaceDB()
                            _tname = name.split("/")[0] if name.startswith("team:") else ""
                            _ws = _wdb.conn.execute(
                                "SELECT workspace_id FROM workspaces "
                                "WHERE director = ?", (_tname,)).fetchone()
                            if not _ws:
                                _ws = _wdb.conn.execute(
                                    "SELECT workspace_id FROM workspaces "
                                    "WHERE director LIKE ? "
                                    "ORDER BY created_at ASC LIMIT 1",
                                    (_tname + "%",)).fetchone()
                            vars_j["project_id"] = _ws["workspace_id"] if _ws else "mw-swarm"
                            _wdb.close()
                    except Exception:
                        vars_j["project_id"] = "mw-swarm"
                    vars_j["role_required"] = ROLE_TO_TASK.get(
                        agent._data.get("role_type"), "")
                    # team_id stable : le plus petit agent_id de la team (ou -1)
                    if name.startswith("team:") and "/" in name:
                        team = name.split("/")[0]
                        row = self.db.conn.execute(
                            "SELECT MIN(agent_id) AS mid FROM agents "
                            "WHERE name LIKE ?", (team + "/%",)).fetchone()
                        vars_j["team_id"] = row["mid"] if row and row["mid"] else -1
                    else:
                        vars_j["team_id"] = -1
                    # Branche auto_code pour les pushes du swarm : ne JAMAIS
                    # pousser sur la branche principale du repo central.
                    vars_j["branch_name"] = f"auto_code_{vars_j['team_id']}"
                    agent.db.conn.execute(
                        "UPDATE agents SET variables_json = ? WHERE agent_id = ?",
                        (_json.dumps(vars_j), agent_id))
                    agent.db.conn.commit()
                    # Sync le snapshot de l'agent : execute() lit self._data.
                    agent._data["variables_json"] = _json.dumps(vars_j)
                except Exception:
                    pass
            agent.execute(request=wakeup_request)
        except Exception as e:
            import traceback
            try:
                log_dir = Path(mw_home()) / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                with open(log_dir / "agent-manager-errors.log", "a",
                          encoding="utf-8") as fh:
                    fh.write(f"[{time.time():.0f}] agent {agent_id} ({wakeup_request}) "
                             f"échec: {e}\n{traceback.format_exc()}\n")
            except Exception:
                pass
        if agent:
            try:
                agent.dehydrate()
            except Exception:
                pass

    def _wake_for_tasks(self) -> int:
        """Réveille les agents greedy en attente (wait_for) quand leur condition
        est remplie.

        Les agents greedy, quand ils n'ont rien à faire, enregistrent une
        condition via le skill workspace/wait_for@v1 (table wait_for, status
        'waiting'). Le waker, à chaque tick :
          - lit les 'waiting' FIFO (les plus anciens d'abord)
          - vérifie si la condition est remplie (issue open / tâche du rôle,
            même workspace + team)
          - réveille les agents concernés UN À LA FOIS par condition
          - marque 'ready' (puis l'agent re-pioche et vérifie)
        Respecte MAX_THREAD_AGENTS. Ne réveille que les agents non hydratés.
        """
        try:
            from modules.sql.workspace import WorkspaceDB
            import json as _json
        except Exception:
            return 0
        # Conditions remplies (état workspace) : issues open + tasks pending
        try:
            wdb = WorkspaceDB()
            open_issues = {
                (i["workspace_id"], i["team_id"])
                for i in wdb.conn.execute(
                    "SELECT workspace_id, team_id FROM issues WHERE status='open'"
                ).fetchall()}
            pending_tasks = {
                (t["workspace_id"], t["team_id"], t["role_required"])
                for t in wdb.conn.execute(
                    "SELECT workspace_id, team_id, role_required FROM tasks "
                    "WHERE status='pending' AND role_required != ''"
                ).fetchall()}
            # Workspaces "terminés" : au moins 1 tâche ET toutes done → le
            # manager peut faire le push de fin sur auto_code_<team_id>.
            all_done = {
                w["workspace_id"]
                for w in wdb.conn.execute(
                    "SELECT workspace_id FROM tasks GROUP BY workspace_id "
                    "HAVING COUNT(*) > 0 "
                    "AND SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) = COUNT(*)"
                ).fetchall()}
        except Exception:
            open_issues = set()
            pending_tasks = set()
            all_done = set()
        finally:
            try:
                wdb.close()
            except Exception:
                pass

        # Agents greedy en attente (waiting), FIFO
        waiting = self.db.wait_for.waiting()
        # (pas de return ici : même sans wait_for, l'amorce réveille les agents
        # greedy au premier cycle)

        def _cond_matches(cond: dict) -> bool:
            ctype = cond.get("type", "")
            ws = cond.get("workspace_id", "")
            team = int(cond.get("team_id", -1))
            if ctype == "issue_open":
                # issue open pour ce workspace (+ team ou projet). Match sur
                # TOUS les workspaces : l'analyste attend une issue, peu importe
                # où elle est.
                return any(t == team or t == -1 for w, t in open_issues)
            if ctype == "task_for_role":
                role = cond.get("role", "")
                # Match par rôle + team sur TOUS les workspaces : l'analyste crée
                # les tasks dans son workspace (ex. audit_io_declarations), pas
                # celui de la team (mw-swarm). L'agent doit être réveillé dès
                # qu'une tâche de son rôle est dispo où qu'elle soit.
                return any((t == team or t == -1) and r == role
                           for w, t, r in pending_tasks)
            if ctype == "workspace_all_done":
                # Le manager pousse quand du travail est fini. Mais s'il n'y a
                # RIEN de nouveau à pousser (branche auto_code déjà à jour sur
                # github), ne pas le réveiller — sinon il boucle : réveil →
                # push (rien) → re-register → réveil…
                if not self._has_unpushed(team):
                    return False
                if ws and ws in all_done:
                    return True
                if not ws:
                    return bool(all_done)
                return False
            return False

        active = len(self.list_active())
        count = 0
        for w in waiting:
            if active + count >= MAX_THREAD_AGENTS:
                break
            # Agent supprimé (team recréée) → purger son wait_for.
            if self.db.conn.execute(
                "SELECT 1 FROM agents WHERE agent_id = ?", (w["agent_id"],)
            ).fetchone() is None:
                self.db.wait_for.mark_done(w["agent_id"])
                continue
            try:
                cond = _json.loads(w["condition"])
            except Exception:
                self.db.wait_for.mark_done(w["agent_id"])
                continue
            if not _cond_matches(cond):
                continue
            # Condition remplie : réveiller l'agent (un à la fois), marquer ready
            ws = cond.get("workspace_id", "")
            req = ("wakeup: issue pending" if cond.get("type") == "issue_open"
                   else "wakeup: task pending")
            if cond.get("type") == "issue_open":
                # Passer le workspace d'une issue ouverte réelle (l'analyste y
                # piochera l'issue).
                if open_issues:
                    for _w, _t in open_issues:
                        if _t == int(cond.get("team_id", -1)) or _t == -1:
                            ws = _w
                            break
            elif cond.get("type") == "workspace_all_done":
                req = "wakeup: workspace done"
                # Passer un workspace réel all-done (pour le push_auto).
                if all_done:
                    ws = next(iter(all_done))
            elif cond.get("type") == "task_for_role":
                # Passer le workspace réel où il y a des tasks (l'analyste les
                # crée dans SON workspace, pas celui de la team).
                _role = cond.get("role", "")
                _team = int(cond.get("team_id", -1))
                for _w, _t, _r in pending_tasks:
                    if _r == _role and (_t == _team or _t == -1):
                        ws = _w
                        break
            self.db.wait_for.mark_ready(w["id"])
            threading.Thread(target=self._run_sleeping_agent,
                             args=(w["agent_id"], req, ws),
                             daemon=True).start()
            count += 1

        # AMORCE : si aucun agent n'est encore en wait_for (premier cycle) mais
        # qu'il y a du travail et des agents greedy non hydratés, on les réveille
        # pour lancer le swarm. Le wait_for prend le relais ensuite (re-endormir).
        if count == 0 and (open_issues or pending_tasks):
            rows = self.db.conn.execute("""
                SELECT agent_id, name, role_type FROM agents
                WHERE (config_json LIKE '%\"pick\"%'
                       OR config_json LIKE '%claim_next%'
                       OR config_json LIKE '%Boucle gloutonne%'
                       OR config_json LIKE '%greedy%'
                       OR occupation = 'continue')
                  AND agent_id NOT IN (SELECT agent_id FROM agent_runtime)
                  AND agent_id NOT IN (SELECT agent_id FROM wait_for WHERE status='waiting')
                LIMIT 20
            """).fetchall()
            for row in rows:
                if active + count >= MAX_THREAD_AGENTS:
                    break
                rt = ROLE_TO_TASK.get(row["role_type"], "")
                # analyste → issue ; rôles greedy → SEULEMENT si des tasks de son
                # rôle (+ team/projet) sont dispo, sinon il re-pioche rien et
                # spam les wait_for.
                if open_issues and rt == "analyst":
                    threading.Thread(target=self._run_sleeping_agent,
                                     args=(row["agent_id"], "wakeup: issue pending",
                                           next(iter({w for w, _ in open_issues}), "")),
                                     daemon=True).start()
                    count += 1
                elif rt:
                    # Réveiller l'intégrateur (merger) quand un workspace est
                    # all-done ET qu'il y a des commits non poussés sur la
                    # branche auto_code (sinon il boucle : réveil → push vide).
                    if rt == "merger" and all_done:
                        _tid = self._agent_team_id(row["agent_id"])
                        if not self._has_unpushed(_tid):
                            continue
                        threading.Thread(target=self._run_sleeping_agent,
                                         args=(row["agent_id"], "wakeup: workspace done",
                                               next(iter(all_done))),
                                         daemon=True).start()
                        count += 1
                        continue
                    # Ne réveiller que si des tasks du RÔLE de l'agent sont dispo
                    # (sinon il re-pioche rien et spam les wait_for).
                    # Ne réveiller que si des tasks du RÔLE de l'agent sont dispo
                    # (sinon il re-pioche rien et spam les wait_for).
                    # Hiérarchie : un rôle senior peut piocher les tâches de
                    # niveau inférieur (coder_senior → coder_junior/mid), donc
                    # on teste si _r (rôle tâche) ∈ _compatible_roles(rt) où rt
                    # est le rôle greedy de l'agent.
                    if not any(
                        _r in _compatible_roles(rt) or _r == rt
                        for _w, _t, _r in pending_tasks):
                        continue
                    threading.Thread(target=self._run_sleeping_agent,
                                     args=(row["agent_id"], "wakeup: task pending",
                                           next(iter({w for w, _, _ in pending_tasks}), "")),
                                     daemon=True).start()
                    count += 1
        return count

    # ── Phase 3 : ressources & préemption ──

    def _agent_resources(self, agent_id: int) -> Dict[str, Any]:
        """Lit et normalise les ressources déclarées d'un agent."""
        row = self.db.conn.execute(
            "SELECT resources_json FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            return {}
        try:
            r = json.loads(row["resources_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            r = {}
        r.setdefault("llm", False)
        r.setdefault("priority", 5)
        r.setdefault("preemptible", True)
        return r

    def evaluate(self, agent_id: int) -> Dict[str, Any]:
        """Évalue si l'agent peut tourner (ressources + LLM) maintenant."""
        from services.ressource_manager.service import RessourceManager
        resources = self._agent_resources(agent_id)
        verdict = RessourceManager().evaluate(resources)
        verdict["agent_id"] = agent_id
        return verdict

    def admit(self, agent_id: int) -> Dict[str, Any]:
        """Admission control + préemption 3 niveaux.

        Si l'agent ne peut pas tourner (ressources/LLM), on tente de préempter
        les agents actifs préemptibles de priorité STRICTEMENT inférieure, puis
        on ré-évalue. Retourne le verdict final + la liste des préemptés.
        """
        verdict = self.evaluate(agent_id)
        if verdict["possible"]:
            return {**verdict, "admitted": True, "preempted": []}

        me = self._agent_resources(agent_id)
        my_priority = me.get("priority", 5)

        active = self.list_active()
        # Candidats préemptibles de priorité inférieure, triés par priorité croissante
        candidates = []
        for a in active:
            res = self._agent_resources(a["agent_id"])
            if res.get("preemptible") and res.get("priority", 5) < my_priority:
                candidates.append((res.get("priority", 5), a["agent_id"]))
        candidates.sort()

        preempted = []
        for _, cid in candidates:
            res = self.kill(cid)  # 3 niveaux : stop → kill → pkill
            preempted.append({"agent_id": cid, "kill": res})
            # Ré-évaluation après chaque préemption
            verdict = self.evaluate(agent_id)
            if verdict["possible"]:
                return {**verdict, "admitted": True, "preempted": preempted}

        verdict = self.evaluate(agent_id)
        return {**verdict, "admitted": verdict["possible"], "preempted": preempted}

    # ── Phase 4 : canal de signaux ──

    VALID_SIGNALS = ("pause", "resume", "wakeup", "sleep", "status", "health", "kill", "configure")

    def send_signal(self, agent_id: int, signal_type: str,
                    payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Enfile un signal pour un agent (canal parallèle de supervision)."""
        if signal_type not in self.VALID_SIGNALS:
            return {"status": "error", "error": f"type de signal invalide: {signal_type}"}
        row = self.db.conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            return {"status": "error", "error": "agent introuvable"}
        cur = self.db.conn.execute(
            "INSERT INTO agent_signals (agent_id, type, payload_json, status) "
            "VALUES (?, ?, ?, 'PENDING')",
            (agent_id, signal_type, json.dumps(payload or {})),
        )
        self.db.conn.commit()
        return {"status": "ok", "signal_id": cur.lastrowid, "type": signal_type,
                "agent_id": agent_id}

    def pending_signals(self, agent_id: int,
                        types: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """Signaux PENDING pour un agent (dans l'ordre)."""
        rows = self.db.conn.execute(
            "SELECT * FROM agent_signals WHERE agent_id = ? AND status = 'PENDING' "
            "ORDER BY signal_id", (agent_id,)
        ).fetchall()
        out = [dict(r) for r in rows]
        if types:
            out = [s for s in out if s["type"] in types]
        return out

    def ack_signal(self, signal_id: int) -> Dict[str, Any]:
        self.db.conn.execute(
            "UPDATE agent_signals SET status = 'ACKED', acknowledged_at = datetime('now') "
            "WHERE signal_id = ?", (signal_id,)
        )
        self.db.conn.commit()
        return {"status": "ok", "signal_id": signal_id}

    def complete_signal(self, signal_id: int,
                        result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Marque un signal COMPLETED, optionnellement en y attachant un résultat."""
        if result is not None:
            row = self.db.conn.execute(
                "SELECT payload_json FROM agent_signals WHERE signal_id = ?", (signal_id,)
            ).fetchone()
            try:
                payload = json.loads(row["payload_json"] or "{}") if row else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            payload["result"] = result
            self.db.conn.execute(
                "UPDATE agent_signals SET status = 'COMPLETED', completed_at = datetime('now'), "
                "payload_json = ? WHERE signal_id = ?",
                (json.dumps(payload), signal_id),
            )
        else:
            self.db.conn.execute(
                "UPDATE agent_signals SET status = 'COMPLETED', completed_at = datetime('now') "
                "WHERE signal_id = ?", (signal_id,)
            )
        self.db.conn.commit()
        return {"status": "ok", "signal_id": signal_id}

    # ── Phase 5 : Agents rares & orchestration ──

    def spawn_agent(
        self, name: str = "", role: str = "", request: str = "",
        occupation: str = "disparate",
        resources: Optional[Dict[str, Any]] = None,
        config: Optional[Dict[str, Any]] = None,
        provider_ref: str = "", model_ref: str = "",
        keep_sleeping: bool = True,
    ) -> Dict[str, Any]:
        if occupation not in ("continue", "noncontinue", "disparate"):
            return {"status": "error", "error": f"occupation invalide: {occupation}"}
        if not role:
            return {"status": "error", "error": "role requis pour spawn_agent"}

        # Vérifier la limite d'agents actifs
        active = len(self.list_active())
        if active >= MAX_THREAD_AGENTS:
            return {"status": "error", "error": f"limite d'agents actifs atteinte ({MAX_THREAD_AGENTS})", "active": active}

        # Vérifier l'espace disque total utilisé par tous les agents
        rows = self.db.conn.execute("SELECT storage_json FROM agents").fetchall()
        total_used = 0
        for row in rows:
            if row["storage_json"]:
                try:
                    s = json.loads(row["storage_json"])
                    total_used += s.get("used_bytes", 0) or 0
                except (json.JSONDecodeError, TypeError):
                    pass
        total_used_gb = total_used / (1024**3)
        if total_used_gb >= MAX_TOTAL_AGENT_DISK_GB:
            return {"status": "error", "error": f"espace disque total des agents épuisé ({total_used_gb:.1f}/{MAX_TOTAL_AGENT_DISK_GB} Go)", "total_used_gb": round(total_used_gb, 1)}

        import shutil
        du = shutil.disk_usage(mw_home())
        free_gb = du.free / (1024**3)
        if free_gb < MIN_DISK_FREE_GB:
            return {"status": "error", "error": f"espace disque insuffisant ({free_gb:.1f} Go libre, minimum {MIN_DISK_FREE_GB} Go)", "free_gb": round(free_gb, 1)}
        name = self._make_agent_name(self.db.conn, role, name)
        ref = f"agent:{name}"
        # Normaliser le config : un workflow ({'steps':...}) doit être
        # encapsulé dans {'workflow': ...} pour qu'Agent.execute le reconnaisse.
        if config and isinstance(config, dict) and "steps" in config and "workflow" not in config:
            config = {"workflow": config}
        try:
            self.db.conn.execute("""
                INSERT INTO agents (name, ref, role_type, occupation, config_json,
                                    resources_json, variables_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (name, ref, role, occupation,
                  json.dumps(config or {}), json.dumps(resources or {}), "{}"))
            self.db.conn.commit()
            agent_id = self.db.conn.execute(
                "SELECT agent_id FROM agents WHERE name = ?", (name,)
            ).fetchone()[0]
            # V0.6.8 : espace disque proprio
            from AgentFrameWork.agent_storage import AgentStorage
            AgentStorage(agent_id, self.db.conn).ensure()
        except Exception as e:
            return {"status": "error", "error": f"création agent: {e}"}

        try:
            agent = Agent.hydrate(agent_id, self.db)
            result = agent.execute(
                request, provider_ref=provider_ref, model_ref=model_ref)
            if keep_sleeping:
                agent.dehydrate()  # → status INIT, runtime supprimé (sommeil BDD)
            else:
                self.db.conn.execute("DELETE FROM agent_runtime WHERE agent_id = ?", (agent_id,))
                self.db.conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
                self.db.conn.commit()
                # V0.6.8 : nettoyer le dossier disque
                from AgentFrameWork.agent_storage import AgentStorage
                AgentStorage(agent_id, self.db.conn).destroy()
            return {"status": "ok", "agent_id": agent_id, "ref": ref,
                    "result": result, "sleeping": keep_sleeping}
        except Exception as e:
            self.db.conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
            self.db.conn.commit()
            from AgentFrameWork.agent_storage import AgentStorage
            AgentStorage(agent_id, self.db.conn).destroy()
            return {"status": "error", "error": str(e)}

    def handoff(self, from_id: int, to_id: int,
                carry_variables: bool = True, carry_state: bool = True) -> Dict[str, Any]:
        """Succession : transfert de session d'un agent vers son successeur.

        Copie variables_json / state_json de `from` vers `to` et chaîne
        from.successor_id = to_id. L'agent `from` retourne dormir (INIT).
        """
        frm = self.db.conn.execute(
            "SELECT variables_json, state_json FROM agents WHERE agent_id = ?", (from_id,)
        ).fetchone()
        to = self.db.conn.execute(
            "SELECT agent_id FROM agents WHERE agent_id = ?", (to_id,)
        ).fetchone()
        if not frm or not to:
            return {"status": "error", "error": "agent source ou cible introuvable"}
        try:
            variables = json.loads(frm["variables_json"] or "{}") if carry_variables else {}
        except (json.JSONDecodeError, TypeError):
            variables = {}
        try:
            state = json.loads(frm["state_json"] or "{}") if carry_state else {}
        except (json.JSONDecodeError, TypeError):
            state = {}
        self.db.conn.execute(
            "UPDATE agents SET variables_json = ?, state_json = ? WHERE agent_id = ?",
            (json.dumps(variables), json.dumps(state), to_id))
        self.db.conn.execute(
            "UPDATE agents SET successor_id = ?, status = 'INIT', last_active_at = datetime('now') "
            "WHERE agent_id = ?", (to_id, from_id))
        self.db.conn.execute("DELETE FROM agent_runtime WHERE agent_id = ?", (from_id,))
        self.db.conn.commit()
        return {"status": "ok", "from_id": from_id, "to_id": to_id,
                "carried_variables": len(variables), "carried_state": len(state)}

    # ── Chat Service = agents role_type='chat' (façades sur le framework) ──

    def create_chat_session(self, name: str = "", system_prompt: str = "",
                            provider_ref: str = "", model_ref: str = "",
                            allow_read_others: bool = False,
                            resources: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Crée une session de chat = agent role_type='chat' (sans exécution).
        name vide → auto-généré `chat_N`."""
        name = self._make_agent_name(self.db.conn, "chat", name)
        config = {"workflow": CHAT_WORKFLOW}
        variables = {
            "messages": [],
            "system_prompt": system_prompt,
            "provider_ref": provider_ref,
            "model_ref": model_ref,
            "allow_read_others": bool(allow_read_others),
        }
        res = resources if resources is not None else {"llm": True}
        res.setdefault("llm", True)
        try:
            self.db.conn.execute(
                "INSERT INTO agents (name, ref, role_type, occupation, config_json, "
                "resources_json, variables_json) VALUES (?, ?, 'chat', 'noncontinue', ?, ?, ?)",
                (name, f"agent:{name}", json.dumps(config),
                 json.dumps(res), json.dumps(variables)))
            self.db.conn.commit()
            aid = self.db.conn.execute(
                "SELECT agent_id FROM agents WHERE name = ?", (name,)).fetchone()[0]
            # V0.6.8 : espace disque proprio
            from AgentFrameWork.agent_storage import AgentStorage
            AgentStorage(aid, self.db.conn).ensure()
            return {"status": "ok", "agent_id": aid, "name": name}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def list_chat_sessions(self) -> Dict[str, Any]:
        rows = self.db.conn.execute(
            "SELECT agent_id, name, status, variables_json, resources_json "
            "FROM agents WHERE role_type = 'chat' ORDER BY name"
        ).fetchall()
        out = []
        for r in rows:
            v = json.loads(r["variables_json"] or "{}")
            out.append({
                "agent_id": r["agent_id"], "name": r["name"], "status": r["status"],
                "provider_ref": v.get("provider_ref", ""),
                "model_ref": v.get("model_ref", ""),
                "allow_read_others": v.get("allow_read_others", False),
                "messages": len(v.get("messages", [])),
            })
        return {"status": "ok", "sessions": out, "count": len(out)}

    def get_chat_session(self, name: str) -> Dict[str, Any]:
        row = self.get_by_name(name)
        if not row:
            return {"status": "error", "error": "session introuvable"}
        v = json.loads(row.get("variables_json") or "{}")
        return {"status": "ok", "agent_id": row["agent_id"], "name": row["name"],
                "provider_ref": v.get("provider_ref", ""),
                "model_ref": v.get("model_ref", ""),
                "system_prompt": v.get("system_prompt", ""),
                "allow_read_others": v.get("allow_read_others", False),
                "messages": v.get("messages", [])}

    def update_chat_session(self, name: str, system_prompt: Optional[str] = None,
                            provider_ref: Optional[str] = None,
                            model_ref: Optional[str] = None,
                            allow_read_others: Optional[bool] = None) -> Dict[str, Any]:
        row = self.get_by_name(name)
        if not row:
            return {"status": "error", "error": "session introuvable"}
        v = json.loads(row.get("variables_json") or "{}")
        if system_prompt is not None:
            v["system_prompt"] = system_prompt
        if provider_ref is not None:
            v["provider_ref"] = provider_ref
        if model_ref is not None:
            v["model_ref"] = model_ref
        if allow_read_others is not None:
            v["allow_read_others"] = bool(allow_read_others)
        self.db.conn.execute(
            "UPDATE agents SET variables_json = ? WHERE agent_id = ?",
            (json.dumps(v), row["agent_id"]))
        self.db.conn.commit()
        return {"status": "ok", "agent_id": row["agent_id"], "name": name}

    def delete_chat_session(self, name: str) -> Dict[str, Any]:
        row = self.get_by_name(name)
        if not row:
            return {"status": "error", "error": "session introuvable"}
        aid = row["agent_id"]
        self.db.conn.execute("DELETE FROM agent_runtime WHERE agent_id = ?", (aid,))
        self.db.conn.execute("DELETE FROM agents WHERE agent_id = ?", (aid,))
        self.db.conn.commit()
        from AgentFrameWork.agent_storage import AgentStorage
        AgentStorage(aid, self.db.conn).destroy()
        stream_bus.reset(aid)
        return {"status": "ok", "agent_id": aid}

    def chat_send(self, name: str, message: str, provider_ref: str = "",
                  model_ref: str = "", stream: bool = False,
                  temperature: float = 0.7, max_tokens: Optional[int] = None) -> Dict[str, Any]:
        row = self.get_by_name(name)
        if not row:
            return {"status": "error", "error": "session introuvable"}
        aid = row["agent_id"]
        v = json.loads(row.get("variables_json") or "{}")
        p_ref = provider_ref or v.get("provider_ref", "")
        m_ref = model_ref or v.get("model_ref", "")
        if not p_ref or not m_ref:
            # Auto-résolution LLM (façade LLMManager, singleton daemon) : la
            # session n'a pas de provider/modèle explicite → on en choisit un.
            try:
                from services.api._shared import _get_llm
                alloc = _get_llm().assign_llm(use_case="chat")
                p_ref = p_ref or alloc.get("provider_ref", "")
                m_ref = m_ref or alloc.get("model_ref", "")
                # persiste pour les tours suivants
                v["provider_ref"] = p_ref
                v["model_ref"] = m_ref
                self.db.conn.execute(
                    "UPDATE agents SET variables_json=? WHERE agent_id=?",
                    (json.dumps(v), aid))
                self.db.conn.commit()
            except Exception:
                pass
        if not p_ref or not m_ref:
            return {"status": "error", "error": "provider/model non défini pour la session"}
        # Modèles FIABLES (testés, avec crédit) essayés en premier, puis
        # assign_llm (best-fallback) en complément.
        RELIABLE: List[Dict[str, str]] = [
            {"provider_ref": "groq", "model_ref": "groq/llama-3.3-70b-versatile"},
            {"provider_ref": "groq", "model_ref": "groq/llama-3.1-8b-instant"},
        ]
        # Tentatives : si un modèle échoue (rate-limit, sans crédit, erreur LLM),
        # on RÉESSAIE avec un autre modèle (best-fallback côté exécution).
        tried: List[str] = []
        last_res: Dict[str, Any] = {"status": "error", "error": "aucune tentative"}
        for attempt in range(6):
            if attempt == 0:
                # 1er essai : modèle fiable groq si la session n'en a pas déjà
                if not (v.get("provider_ref") and v.get("model_ref")):
                    p_ref = RELIABLE[0]["provider_ref"]
                    m_ref = RELIABLE[0]["model_ref"]
            elif attempt <= len(RELIABLE):
                # les autres modèles fiables
                cand = RELIABLE[attempt - 1]
                p_ref = cand["provider_ref"]
                m_ref = cand["model_ref"]
            else:
                # assign_llm en excluant ceux déjà tentés
                try:
                    from services.api._shared import _get_llm
                    alloc = _get_llm().assign_llm(use_case="chat", exclude_models=tried)
                    p_ref = alloc.get("provider_ref", "")
                    m_ref = alloc.get("model_ref", "")
                except Exception:
                    break
            tried.append(f"{p_ref}/{m_ref}")
            try:
                agent = Agent.hydrate(aid, self.db)
                res = agent.chat_turn(
                    message, provider_ref=p_ref, model_ref=m_ref,
                    temperature=temperature, max_tokens=max_tokens or 4096,
                    system_prompt=v.get("system_prompt", ""))
                agent.dehydrate()
                last_res = res
            except Exception as e:
                last_res = {"status": "error", "error": str(e)}
            if last_res.get("status") in ("ok", "success"):
                # persiste le modèle fonctionnel pour les tours suivants
                v["provider_ref"] = p_ref
                v["model_ref"] = m_ref
                self.db.conn.execute(
                    "UPDATE agents SET variables_json=? WHERE agent_id=?",
                    (json.dumps(v), aid))
                self.db.conn.commit()
                return {"status": "ok", "agent_id": aid, "name": name,
                    "reply": res.get("reply", ""),
                    "model": f"{p_ref}/{m_ref}", "usage": {},
                    "messages": len(res.get("messages", []))}
        # Tous les modèles ont échoué → retourne la dernière erreur.
        last_res["error"] = f"{last_res.get('error','')} (modèles testés: {', '.join(tried)})"
        return last_res

    def chat_read(self, name: str, other: str) -> Dict[str, Any]:
        """Lit l'historique d'une AUTRE session (si celle-ci l'autorise)."""
        row = self.get_by_name(name)
        if not row:
            return {"status": "error", "error": "session lectrice introuvable"}
        v = json.loads(row.get("variables_json") or "{}")
        if not v.get("allow_read_others", False):
            return {"status": "error",
                    "error": "cette session n'a pas le droit de lire les autres"}
        orow = self.get_by_name(other)
        if not orow:
            return {"status": "error", "error": "session cible introuvable"}
        ov = json.loads(orow.get("variables_json") or "{}")
        return {"status": "ok", "reader": name, "source": other,
                "messages": ov.get("messages", [])}

    # ── Interne ──

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


    @staticmethod
    def _make_agent_name(conn, role: str, proposed_name: str = "") -> str:
        """Génère un nom unique d'agent : si proposed_name non vide, le
        prend tel quel (collision → suffixe). Sinon, génère format `role_N`
        (ex: assistant_3)."""
        if proposed_name:
            name = proposed_name
            suffix = 1
            while conn.execute(
                "SELECT 1 FROM agents WHERE name = ?", (name,)).fetchone():
                name = f"{proposed_name}_{suffix}"
                suffix += 1
            return name
        cur = conn.execute(
            "SELECT COUNT(*) + 1 FROM agents WHERE role_type = ?",
            (role,),
        )
        n = cur.fetchone()[0]
        name = f"{role}_{n}"
        while conn.execute(
            "SELECT 1 FROM agents WHERE name = ?", (name,)).fetchone():
            n += 1
            name = f"{role}_{n}"
        return name


# ──────────────────────────────────────────────
#  Entrypoint service (pour le superviseur)
# ──────────────────────────────────────────────

def run_service(interval: float = 5.0):
    """Boucle de supervision. Tourne en continu."""
    if not acquire_instance_lock("agent_manager"):
        return

    manager = AgentManager()
    while True:
        try:
            result = manager.tick()
            if result["zombies_found"] > 0:
                print(json.dumps(result), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    run_service()
