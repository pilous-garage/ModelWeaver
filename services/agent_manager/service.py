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
from modules.llm_manager.litellm_bridge import LiteLLMBridge
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


def make_bridge() -> "LiteLLMBridge":
    """Construit un bridge LiteLLM avec Key Manager (clés BDD) + catalogue."""
    return LiteLLMBridge(cat=_get_catalogue_db(), km=_get_key_manager())


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

class Agent:
    """Wrapper runtime d'un agent hydraté.

    L'agent existe en BDD (table agents). Cette classe est une enveloppe
    pour interagir avec lui pendant son cycle d'exécution.
    """

    def __init__(self, db: AgentsDB, agent_data: Dict[str, Any]):
        self.db = db
        self._data = agent_data
        self._bridge: Optional[LiteLLMBridge] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

        self.agent_id = agent_data["agent_id"]
        self.name = agent_data["name"]
        self.ref = agent_data["ref"]
        self.role_type = agent_data["role_type"]
        self.occupation = agent_data["occupation"]
        self.status = agent_data["status"]

        # Lifecycle hooks
        config = json.loads(self._data.get("config_json") or "{}")
        from services.lifecycle import LifecycleManager
        self._lifecycle = LifecycleManager(self.agent_id, config)

    @classmethod
    def hydrate(cls, agent_id: int, db: Optional[AgentsDB] = None) -> "Agent":
        """Hydrate un agent depuis la BDD.

        Charge les données, vérifie l'existence, crée une entrée runtime.
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
                    result = self._bridge.chat(
                        provider_ref=provider_ref,
                        model_ref=model_ref,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    content = result.content if hasattr(result, 'content') else str(result)
                    tokens = 0
                    if hasattr(result, 'usage') and isinstance(result.usage, dict):
                        tokens = result.usage.get("total_tokens", 0)
                    budget = getattr(result, 'budget', {}) or {}
                    stream_sink(content)
                    result = {"status": "ok", "content": content, "tokens_used": tokens,
                              "budget": budget}
                finally:
                    _hb_stop.set()

            # Enregistrer les métriques
            db = self.db
            tokens = result.get("tokens_used", 0)
            success = result.get("status") in ("ok", "success")
            db.conn.execute("""
                INSERT INTO agent_metrics (agent_id, total_tasks, total_tokens,
                                           failed_tasks)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    total_tasks = total_tasks + 1,
                    total_tokens = total_tokens + ?,
                    failed_tasks = failed_tasks + ?,
                    last_updated = datetime('now')
            """, (self.agent_id, tokens, 0 if success else 1,
                  tokens, 0 if success else 1))
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
        stream_sink = lambda chunk: stream_bus.publish(self.agent_id, chunk, "token")
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
                    "budget": result.budget}
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
        agent_call_handler: Any = None, lifecycle_mgr: Any = None,
    ) -> "FSMResult":
        """Exécution via FSM Interpreter (Phase 4 : signaux + streaming,
        Phase 5 : spawn + handoff)."""
        import json
        variables = json.loads(self._data.get("variables_json") or "{}")
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
            client = get_afd_client()
            return client.call(
                agent_name, "execute",
                request=inputs.get("request", ""),
                entrypoint=entrypoint,
                provider_ref=inputs.get("provider_ref", ""),
                model_ref=inputs.get("model_ref", ""),
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
        """Déshydrate l'agent : sauve état, supprime runtime, libère le thread."""
        db = self.db
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
            WHERE a.status IN ('RUNNING', 'IDLE')
              AND r.heartbeat_at < datetime('now', ?)
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

        active = len(self.list_active())

        return {
            "status": "ok",
            "active_agents": active,
            "zombies_found": len(zombies),
            "zombies_killed": killed,
        }

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

    VALID_SIGNALS = ("pause", "resume", "status", "health", "kill", "configure")

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
        """Crée un agent (souvent occupation `disparate` = rare/on-demand),
        l'hydrate, l'exécute, puis le renvoie dormir en BDD (Phénix).

        keep_sleeping=True : après exécution l'agent reste en BDD (status INIT),
        prêt à réveiller. False : il est supprimé.
        name vide → auto-généré format `role_N` (ex: assistant_3).
        """
        if occupation not in ("continue", "noncontinue", "disparate"):
            return {"status": "error", "error": f"occupation invalide: {occupation}"}
        if not role:
            return {"status": "error", "error": "role requis pour spawn_agent"}
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
            return {"status": "error", "error": "provider/model non défini pour la session"}
        try:
            agent = Agent.hydrate(aid, self.db)
            res = agent.chat_turn(
                message, provider_ref=p_ref, model_ref=m_ref,
                temperature=temperature, max_tokens=max_tokens or 4096,
                system_prompt=v.get("system_prompt", ""))
            agent.dehydrate()
            if res.get("status") not in ("ok", "success"):
                return res
            return {"status": "ok", "agent_id": aid, "name": name,
                    "reply": res.get("reply", ""),
                    "model": f"{p_ref}/{m_ref}", "usage": {},
                    "messages": len(res.get("messages", []))}
        except Exception as e:
            return {"status": "error", "error": str(e)}

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
