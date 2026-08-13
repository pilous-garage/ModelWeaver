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
MIN_ACTIVE_TARGET = 10     # cible d'agents greedy ACTIFS simultanés (le waker
                           # complète jusqu'à ce seuil quand du travail est dispo)
MIN_REVIEWERS_ACTIVE = 4   # reviewers greedy MINIMUM actifs quand un backlog
                           # review existe (priorité : valider les livrables
                           # avant de produire encore plus de tâches à relire)
MAX_SLEEPING_AGENTS = 10000  # agents endormis max dans la BDD
MIN_DISK_FREE_GB = 1       # espace disque libre minimum avant de créer un agent
MAX_TOTAL_AGENT_DISK_GB = 10  # espace disque total max utilisé par tous les agents

# Registre en mémoire des threads agents EN VIE. Garantie anti re-spawn : même
# si agent_runtime est purgé en BDD (ex. watcher P8, restart partiel), on ne
# re-hydrate JAMAIS un agent dont le thread Python tourne encore — sinon on
# empile des clones (1000 threads / 8000 fds observés).
_LIVE_AGENT_THREADS: Dict[int, threading.Thread] = {}
_LIVE_LOCK = threading.Lock()

# Cooldown de ré-armement wait_for après un échec de run (secondes). Évite le
# spam wait_for→réveil→échec→wait_for toutes les 5s quand un agent échoue vite
# (modèle mort, exception rapide). On n'arme que 1 fois / cooldown.
REARM_WAITFOR_COOLDOWN_S = 20.0
_LAST_REARM_AT: Dict[int, float] = {}


def _agent_thread_alive(agent_id: int) -> bool:
    with _LIVE_LOCK:
        th = _LIVE_AGENT_THREADS.get(agent_id)
        return th is not None and th.is_alive()


def _live_agent_threads_count() -> int:
    """Nombre de threads agents actuellement vivants (agents greedy en run).

    Reflète la VRAIE activité : les agents greedy s'exécutent dans des threads
    daemon enregistrés dans _LIVE_AGENT_THREADS (inscrits au début de
    _run_sleeping_agent, retirés à la fin). C'est plus fiable que agent_runtime
    (qui peut être purgé par le watcher P3/P8 pendant un run long).
    """
    with _LIVE_LOCK:
        return sum(1 for th in _LIVE_AGENT_THREADS.values() if th.is_alive())


def _live_agent_thread_ids() -> List[int]:
    """IDs des agents greedy actuellement en run (threads vivants)."""
    with _LIVE_LOCK:
        return [aid for aid, th in _LIVE_AGENT_THREADS.items() if th.is_alive()]


# Mapping role_type d'agent → rôle requis par une tâche (workspace greedy).
# Task_type PIOCHÉ par chaque rôle d'agent (waker + wait_for). Un agent
# codeur pioche les tokens coding, un relecteur les code_review, un
# test_runner les testing_code, un orchestrateur les merger_code.
ROLE_TO_TASK = {
    "architecte": "analysis",
    "planificateur": "analysis",
    "explorateur": "exploration",
    "explore": "exploration",
    "codeur": "coding",
    "test_runner": "testing_code",
    "relecteur": "code_review",
    "orchestrateur": "merger_code",
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
        self._owns_db = False  # True si hydrate a reçu un db DÉDIÉ → à fermer

        # Lifecycle hooks
        config = json.loads(self._data.get("config_json") or "{}")
        from services.lifecycle import LifecycleManager
        self._lifecycle = LifecycleManager(self.agent_id, config)

    @classmethod
    def hydrate(cls, agent_id: int, db: Optional[AgentsDB] = None) -> "Agent":
        """Hydrate un agent depuis la BDD.

        Charge les données, vérifie l'existence, crée une entrée runtime
        et initialise le shell interne de l'agent.

        GARDE-FOU : un agent DÉJÀ hydraté (présent dans agent_runtime) ne peut
        pas être hydraté à nouveau — sinon 2 threads exécutent le même agent
        en parallèle (pollution du StreamBus, fall-backs mélangés dans le chat,
        state LLM corrompu). Lève une RuntimeError dans ce cas.
        """
        db = db or AgentsDB()
        row = db.conn.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Agent {agent_id} introuvable")

        # Déjà actif ? Refuser la double hydratation (le waker et dev-chat
        # doivent vérifier AVANT d'appeler hydrate).
        already = db.conn.execute(
            "SELECT agent_id FROM agent_runtime WHERE agent_id = ?",
            (agent_id,)
        ).fetchone()
        if already:
            raise RuntimeError(
                f"Agent {agent_id} déjà hydraté/en exécution — attendre la fin "
                f"du run avant de le relancer (agent_runtime présent)")

        self = cls(db, dict(row))
        # Si un db DÉDIÉ (non partagé) est passé, l'agent en est propriétaire
        # → dehydrate() le fermera (évite la fuite de fds SQLite par run).
        self._owns_db = db is not None

        # Marquer comme IDLE dans la BDD
        db.conn.execute(
            "UPDATE agents SET status = 'IDLE', last_active_at = datetime('now') "
            "WHERE agent_id = ?", (agent_id,)
        )

        # Créer l'entrée runtime (thread actif)
        thread_id = f"agent:{self.name}:{int(time.time())}"
        # Le propriétaire / la team survivent à la déshydratation (colonnes
        # persistées dans `agents`) ; on les reflète dans agent_runtime.
        owner = row["id_proprietaire"] if "id_proprietaire" in row.keys() else None
        team = row["id_team"] if "id_team" in row.keys() else None
        db.conn.execute("""
            INSERT INTO agent_runtime
                (agent_id, thread_id, pid, heartbeat_at, started_at, current_step,
                 id_proprietaire, id_team)
            VALUES (?, ?, ?, datetime('now'), datetime('now'), 'hydrated', ?, ?)
        """, (agent_id, thread_id, os.getpid(), owner, team))
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
        # RESET des variables de RUN greedy à chaque exécution : l'ancienne
        # tâche (task/repo_eff/work_out/review_out/…) ne doit jamais polluer le
        # nouveau run — c'est le picker qui repart d'un état propre. On GARDE
        # les variables « logiques » persistantes : _llm_provider/_llm_model/
        # _llm_fallbacks (LLM assigné), workspace_id/team_id/project_id/
        # role_required, agent_id, home.
        _KEEP = ("agent_id", "home", "workspace_id", "team_id", "project_id",
                 "role_required", "_llm_provider", "_llm_model", "_llm_fallbacks",
                 "request", "exclude_models", "restrict_llm")
        for _k in [k for k in variables
                   if k not in _KEEP
                   and not k.startswith("_")
                   and k != "messages"]:
            del variables[_k]
        for _k in [k for k in variables
                   if k.startswith("_") and k not in _KEEP
                   and k not in ("_last_call_ok", "_last_call_error")]:
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

        # Fermer la connexion BDD de cet agent (thread_db du thread) : sinon
        # chaque run laisse des fds SQLite ouverts à vie (fuite → contention).
        # Best-effort : la connexion partagée self.db.conn n'est PAS fermée ici
        # (le manager en a besoin), seules les connexions dédiées le sont.
        if getattr(self, "_owns_db", False):
            try:
                db.close()
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
        # Réveiller périodiquement le COORDINATEUR (surveillant) : il explore
        # le taskflow et vérifie la complétion chat → swarm → chat, même sans
        # tâches à piocher.
        woken_coord = self._wake_coordinator()

        # V0.15 taskflow : tick FAILSAFE du task_supervisor (léger, rythme
        # lent). Le chemin nominal est synchrone (skill task_ask_new → assign) ;
        # ici on rattrape les cas passifs (dépendances satisfaites, règles à
        # appliquer, tâches à finaliser) quand personne n'a demandé.
        supervised = self._supervise_taskflow()

        active = len(self.list_active())
        # ACTIVITÉ RÉELLE : les agents greedy s'exécutent dans des threads
        # daemon enregistrés dans _LIVE_AGENT_THREADS. Un agent qui travaille
        # FLUSH des observations de capacité (capacite_log → table d'expérience)
        # par batch : mis à jour à chaque tick pour que la table de capacités
        # reflète l'usage réel des modèles (agentic / agentic-translation).
        try:
            from modules.llm_manager.capacites import flush_capacite_log
            from modules.sql.catalogue_repo import CatalogueDB
            _ccat = CatalogueDB()
            flush_capacite_log(_ccat)
            _ccat.close()
        except Exception:
            pass
        # (même en attente LLM, pas encore dans agent_runtime à cet instant)
        # compte comme actif. Sans ça, `active_agents` sous-estime massivement
        # le swarm (les threads vivants vs agent_runtime).
        active = max(active, _live_agent_threads_count())
        # Partager la vraie activité avec les AUTRES process (API daemon, GUI) :
        # ils ne voient pas _LIVE_AGENT_THREADS (registre en mémoire par
        # process). On écrit le compteur dans meta.agents_active à chaque tick.
        try:
            self.db.conn.execute(
                "INSERT INTO meta(key, value) VALUES ('agents_active', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (active,))
            self.db.conn.commit()
        except Exception:
            pass
        # SYNCHRO STATUT : marque RUNNING les greedy en thread vivant et remet
        # les autres à INIT (les panneaux GUI lisent `agents.status` — sans ça
        # ils affichent IDLE/INIT pour des agents qui travaillent réellement
        # dans des threads hors agent_runtime).
        try:
            live_ids = list(_live_agent_thread_ids())
            if live_ids:
                ph = ",".join("?" for _ in live_ids)
                self.db.conn.execute(
                    f"UPDATE agents SET status = 'RUNNING', last_active_at = datetime('now') "
                    f"WHERE agent_id IN ({ph})", tuple(live_ids))
            self.db.conn.commit()
        except Exception:
            pass

        return {
            "status": "ok",
            "active_agents": active,
            "zombies_found": len(zombies),
            "zombies_killed": killed,
            "woken_agents": woken,
            "woken_tasks": woken_tasks,
            "woken_coordinator": woken_coord,
            "tasks_reclaimed": reclaimed,
            "issues_completed": issues_completed,
            "supervised": supervised,
        }

    _SUPERVISE_INTERVAL = 15.0   # tick failsafe du taskflow (s)
    _supervise_last: float = 0.0

    def _supervise_taskflow(self, force: bool = False) -> int:
        """Tick FAILSAFE du task_supervisor (léger).

        Une passe de supervision pour chaque (workspace, team) présent dans
        sub_tasks, à intervalle lent. Le chemin nominal reste synchrone
        (task_ask_new). Retourne le nombre total de sub_tasks traitées."""
        import time as _t
        now = _t.time()
        if not force and now - self._supervise_last < self._SUPERVISE_INTERVAL:
            return 0
        self._supervise_last = now
        try:
            from services.task_supervisor.service import TaskSupervisor
            from modules.sql.workspace import WorkspaceDB
            wdb = WorkspaceDB()
            rows = wdb.conn.execute(
                "SELECT DISTINCT workspace_id, team_id FROM sub_tasks "
                "WHERE team_id != -1").fetchall()
            sup = TaskSupervisor(wdb)
            total = 0
            for r in rows:
                res = sup.supervise_team(r["workspace_id"], r["team_id"])
                total += (res["created"] + res["released"]
                          + res["supervised"] + res["tasks_finalized"])
            return total
        except Exception:
            return 0

    _COORD_INTERVAL = 90.0     # réveil périodique du coordinateur (s)
    _coord_last: float = 0.0   # timestamp du dernier réveil

    def _wake_coordinator(self) -> int:
        """Réveille périodiquement le COORDINATEUR (agent surveillant).

        Le coordinateur explore le taskflow (tâches, agents, livrables) et
        vérifie la complétion de bout en bout — il doit tourner même sans
        tâche à piocher. Réveillé toutes les _COORD_INTERVAL secondes s'il
        n'est pas déjà actif (pas de thread concurrent).
        """
        import time as _t
        now = _t.time()
        if now - self._coord_last < self._COORD_INTERVAL:
            return 0
        self._coord_last = now
        try:
            row = self.db.conn.execute(
                "SELECT agent_id FROM agents WHERE name = 'team:dev-chat/surveillant' "
                "LIMIT 1").fetchone()
            if not row:
                return 0
            aid = row["agent_id"] if not isinstance(row, tuple) else row[0]
            # Déjà actif ? → skip (évite les doubles cycles concurrents).
            active = {r["agent_id"] for r in self.db.conn.execute(
                "SELECT agent_id FROM agent_runtime").fetchall()}
            if aid in active:
                return 0
            if _agent_thread_alive(aid):
                return 0
            threading.Thread(
                target=self._run_sleeping_agent,
                args=(aid, "wakeup: cycle coordination", "mw-dev-chat"),
                daemon=True).start()
            return 1
        except Exception:
            return 0

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

    def get_team(self, agent_id: int) -> Dict[str, Any]:
        """Retourne la team d'un agent (nom + team_id stable).

        Résultat : {team_id, team_name, members:[{agent_id,name,role_type}]}.
        team_id = MIN(agent_id) de la team (clé stable utilisée par le
        workspace). Best-effort : team_id=-1 et liste vide si pas en team.
        """
        try:
            row = self.db.conn.execute(
                "SELECT name FROM agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            if not row or not row["name"].startswith("team:"):
                return {"team_id": -1, "team_name": "", "members": []}
            team = row["name"].split("/")[0]
            team_id = self._agent_team_id(agent_id)
            members = [dict(r) for r in self.db.conn.execute(
                "SELECT agent_id, name, role_type FROM agents "
                "WHERE name LIKE ? ORDER BY agent_id", (team + "/%",)).fetchall()]
            return {"team_id": team_id, "team_name": team,
                    "members": members}
        except Exception:
            return {"team_id": -1, "team_name": "", "members": []}

    def get_leader(self, team_id: int) -> Optional[Dict[str, Any]]:
        """Retourne le leader (orchestrateur) d'une team.

        Le leader est l'agent `role_type='orchestrateur'` de la team (le
        « security_clearance » : il valide/refuse les autorisations des
        membres via un call LLM). Retourne {agent_id, name, role_type}.
        """
        try:
            # team_id = MIN(agent_id) de la team → retrouver le nom de team.
            row = self.db.conn.execute(
                "SELECT name FROM agents WHERE agent_id = ?", (team_id,)
            ).fetchone()
            if not row or not row["name"].startswith("team:"):
                return None
            team = row["name"].split("/")[0]
            leader = self.db.conn.execute(
                "SELECT agent_id, name, role_type FROM agents "
                "WHERE name LIKE ? AND role_type = 'orchestrateur' "
                "ORDER BY agent_id LIMIT 1", (team + "/%",)).fetchone()
            return dict(leader) if leader else None
        except Exception:
            return None

    def _agent_paused(self, name: str) -> bool:
        """Vrai si l'agent appartient à une équipe en PAUSE (level=team).

        Une team mise en pause (ex. swarm-selfimprove-v2 suspendu) ne doit pas
        réveiller ses greedy : sans ce garde, le waker relance les codeurs en
        boucle (statut INIT/RUNNING) malgré la pause posée via pause/set.
        Best-effort : False si le store est indisponible (on ne bloque pas la
        supervision pour une erreur de lecture).
        """
        if not name or not name.startswith("team:"):
            return False
        try:
            team = name.split("/")[0][len("team:"):]
            if not team:
                return False
            from AgentFrameWork.pause_flag_store import get_paused
            return get_paused("team", team)
        except Exception:
            pass
        return False

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
        """Remet les tasks 'doing'/'running' à 'todo' si plus aucun agent n'est
        en cours d'exécution légitime.

        Un agent "légitimement actif" = présent dans agent_runtime ET de statut
        RUNNING/IDLE (les reliquats de runs morts ont un statut INIT/None après
        redémarrage). Si aucun agent ne tourne, toutes les tasks 'doing'/
        'running' sont orphelines → on les libère pour qu'un agent les reprenne
        (sinon le waker/amorce ne voit que des 'todo' et ne réveille personne).
        """
        try:
            n_active_runtime = self.db.conn.execute("""
                SELECT COUNT(*) FROM agent_runtime r
                JOIN agents a ON a.agent_id = r.agent_id
                WHERE a.status IN ('RUNNING', 'IDLE')
            """).fetchone()[0]
            # Les greedy tournent en THREADS (enregistrés dans _LIVE_AGENT_THREADS,
            # parfois pas encore dans agent_runtime) → ils comptent comme actifs.
            # Un agent marqué RUNNING (même sans thread runtime encore visible)
            # est aussi actif — sinon le reclaim re-claim les tâches d'un run
            # fraîchement lancé et le coupe (boucle reviewer).
            n_running = self.db.conn.execute(
                "SELECT COUNT(*) FROM agents WHERE status = 'RUNNING'"
            ).fetchone()[0]
            n_active = max(n_active_runtime, _live_agent_threads_count(),
                           n_running)
            if n_active > 0:
                return 0
            from modules.sql.workspace import WorkspaceDB
            wdb = WorkspaceDB()
            # Ne re-claimer que les tâches doing 'STALES' (assignées depuis
            # plus de RECLAIM_MIN_AGE_MIN) : une tâche fraîchement assignée
            # peut être entre deux steps d'un run (runtime pas encore visible)
            # — la re-claimer la couperait en boucle (reviewer re-pick).
            reclaim_age = getattr(self, "RECLAIM_MIN_AGE_MIN", 15)
            n = wdb.conn.execute(
                "UPDATE tasks SET status = 'todo', assigned_to = '', "
                "updated_at = datetime('now') "
                "WHERE status IN ('doing', 'running') "
                "AND (julianday('now') - julianday(updated_at)) * 1440 >= ?",
                (reclaim_age,)).rowcount
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
            # Garde-fou mémoire : ne pas re-hydrater un agent dont un thread
            # tourne encore (même si agent_runtime a été purgé par P8).
            if _agent_thread_alive(agent_id):
                continue
            # Garde pause : une team en pause ne réveille pas ses agents.
            if self._agent_paused(row["name"]):
                continue
            threading.Thread(target=self._run_sleeping_agent, args=(agent_id,), daemon=True).start()
            count += 1
        return count

    def _run_sleeping_agent(self, agent_id: int, wakeup_request: str = "wakeup: signals pending",
                            workspace_id: str = "") -> None:
        # Garde-fou anti re-spawn : si un thread du MÊME agent tourne déjà (même
        # si agent_runtime a été purgé en BDD), on ne repart pas — évite
        # l'accumulation de clones bloqués sur des appels LLM lents.
        if _agent_thread_alive(agent_id):
            return
        # RÉSERVATION ATOMIQUE : on inscrit le thread DANS le lock AVANT la
        # purge et hydrate. Sans ça, deux réveils concurrents passent tous les
        # deux le garde alive=False puis l'un échoue sur UNIQUE agent_runtime
        # (course observée). Le premier réservé gagne ; le second sort.
        # NB : on vérifie _LIVE_AGENT_THREADS DIRECTEMENT (pas via
        # _agent_thread_alive qui re-prend le lock → deadlock).
        with _LIVE_LOCK:
            _existing = _LIVE_AGENT_THREADS.get(agent_id)
            if _existing is not None and _existing.is_alive():
                return
            _LIVE_AGENT_THREADS[agent_id] = threading.current_thread()
        # Nettoyage des agent_runtime ORPHELINS : si une entrée runtime existe
        # mais que le thread de l'agent est MORT (invisible en mémoire), c'est
        # un résidu d'un run tué/crashé → hydrate lèverait « déjà hydraté »
        # pour toujours et le waker bouclerait (réveil → échec → réveil).
        # On purge l'entrée orpheline avant de relancer proprement.
        try:
            alive_rt = self.db.conn.execute(
                "SELECT agent_id FROM agent_runtime WHERE agent_id = ?",
                (agent_id,)).fetchone()
            if alive_rt and not _agent_thread_alive(agent_id):
                self.db.conn.execute(
                    "DELETE FROM agent_runtime WHERE agent_id = ?",
                    (agent_id,))
                self.db.conn.commit()
        except Exception:
            pass
        agent = None
        thread_db = None
        try:
            # Connexion DÉDIÉE au thread : la connexion partagée self.db.conn
            # n'est pas thread-safe (sqlite3) → "bad parameter or API misuse"
            # quand plusieurs réveils greedy hydratent des agents en parallèle.
            from modules.sql.db import AgentsDB
            thread_db = AgentsDB()
            agent = Agent.hydrate(agent_id, db=thread_db)
            if workspace_id or True:
                # Injecte le workspace + le rôle greedy + team_id dans les
                # variables : l'agent sait où piocher, avec quel rôle, et dans
                # quelle team. team_id = id du plus ancien membre de la team
                # (stable par team) ; -1 si pas de team.
                # Toujours résoudre le workspace DEPUIS LE MANIFEST de la team
                # (source de vérité) : sans ça un agent réveillé sans ws garde
                # une variable héritée d'un ancien workspace (pause-resume-
                # daemon) et pioche au mauvais endroit.
                try:
                    import json as _json
                    name = agent._data.get("name", "")
                    # 0) Source de vérité : le manifest de la team (workspace_id)
                    from services.team_spec import TeamSpec
                    _team_name = name.split("/")[0][len("team:"):] if name.startswith("team:") else ""
                    _manifest_ws = ""
                    _manifest_proj = ""
                    if _team_name:
                        try:
                            _spec = TeamSpec.from_yaml(
                                f"services/manifests/teams/{_team_name}.team.yaml")
                            # project_id git = le REPO central de la team
                            # (manifest project_id/repo), sinon le workspace.
                            _manifest_proj = _spec.project_id or _spec.workspace_id or ""
                            _manifest_ws = _spec.workspace_id or ""
                        except Exception:
                            _manifest_ws = ""
                    # Le workspace de PIOCHAGE des tâches = le workspace de la
                    # team (manifest, source de vérité). On l'emporte sur le ws
                    # passé par le waker s'il vient d'un wait_for obsolète
                    # (l'agent s'était endormi sur un ancien workspace). Un ws
                    # explicite n'est respecté que pour les agents HORS team
                    # (pas de manifest) ou si le manifest n'en définit pas.
                    _use_ws = _manifest_ws or workspace_id or "mw-dev-chat"
                    vars_j = _json.loads(agent._data.get("variables_json") or "{}")
                    vars_j["workspace_id"] = _use_ws
                    # project_id git = le workspace de la TEAM (repo central de
                    # référence, ex. mw-swarm), pas le workspace des tâches.
                    if _manifest_proj:
                        vars_j["project_id"] = _manifest_proj
                    else:
                        # fallback : workspace lié à la team (director)
                        try:
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
                    # RESET des variables LOGIQUES de boucle à chaque réveil :
                    # un flag résiduel (ex. work_done='1' laissé par un run
                    # précédent) fait sauter le working_loop immédiatement →
                    # le greedy ne code jamais (bug observé). On remet les
                    # flags de contrôle à leur valeur initiale, SANS toucher
                    # aux compteurs/tokens (agent_id, tokens accumulés).
                    for _logical in ("work_done", "has_llm", "work_out",
                                     "check_out", "verif_files"):
                        if _logical in vars_j:
                            del vars_j[_logical]
                    agent.db.conn.execute(
                        "UPDATE agents SET variables_json = ? WHERE agent_id = ?",
                        (_json.dumps(vars_j), agent_id))
                    agent.db.conn.commit()
                    # Sync le snapshot de l'agent : execute() lit self._data.
                    agent._data["variables_json"] = _json.dumps(vars_j)
                except Exception:
                    pass
            run_result = agent.execute(request=wakeup_request)
        except Exception as e:
            import traceback
            run_result = None
            try:
                log_dir = Path(mw_home()) / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                with open(log_dir / "agent-manager-errors.log", "a",
                          encoding="utf-8") as fh:
                    fh.write(f"[{time.time():.0f}] agent {agent_id} ({wakeup_request}) "
                             f"échec: {e}\n{traceback.format_exc()}\n")
            except Exception:
                pass
        finally:
            # Réveil auto après ÉCHEC de run (pas un endormissement volontaire) :
            # si l'agent s'est arrêté sur une série de LLM en échec (llm_timeout,
            # no_action, max_loops, tool_loop_break…) sans avoir pu s'endormir
            # proprement via wait_for, on ré-enregistre son wait_for pour que le
            # waker le re-réveille au prochain tick (il retentera avec des LLM
            # potentiellement re-disponibles). Un greedy échoue souvent au 1er
            # essai (modèle mort) puis réussit au suivant — sans ce ré-armement
            # il resterait INIT/endormi pour toujours.
            try:
                _sig = ""
                _code = 1
                if isinstance(run_result, dict):
                    _sig = str(run_result.get("signal")
                               or run_result.get("status")
                               or run_result.get("end_reason")
                               or "")
                    _code = int(run_result.get("exit_code")
                                or (1 if run_result.get("status") in
                                    ("failed", "error", "aborted") else 0))
                _failed_sig = _sig in (
                    "llm_timeout", "no_action", "max_loops", "tool_loop_break",
                    "llm_error", "pool_saturated", "error", "failed", "aborted")
                # Ré-armer le wait_for UNIQUEMENT pour les greedy (occupation
                # continue) — les agents non-greedy (chat-pilot, etc.) ne doivent
                # pas boucler.
                _occupation = ""
                try:
                    _r = self.db.conn.execute(
                        "SELECT occupation FROM agents WHERE agent_id = ?",
                        (agent_id,)).fetchone()
                    if _r:
                        _occupation = _r["occupation"] or ""
                except Exception:
                    pass
                if _failed_sig and _occupation == "continue" and not _agent_thread_alive(agent_id):
                    # Cooldown : ne pas spammer wait_for si l'agent échoue en
                    # boucle rapide (modèle mort). On ré-arme au plus 1 fois
                    # par REARM_WAITFOR_COOLDOWN_S.
                    _rearm = False
                    _now_t = time.time()
                    with _LIVE_LOCK:
                        _last = _LAST_REARM_AT.get(agent_id, 0.0)
                        if _now_t - _last >= REARM_WAITFOR_COOLDOWN_S:
                            _LAST_REARM_AT[agent_id] = _now_t
                            _rearm = True
                    if _rearm:
                        _role_req = ROLE_TO_TASK.get(
                            (agent._data.get("role_type") if agent else ""), "")
                        _ws = ""
                        if agent:
                            try:
                                import json as _aj
                                _v = _aj.loads(agent._data.get("variables_json") or "{}")
                                _ws = _v.get("workspace_id", "") or "mw-dev-chat"
                                _role_req = _v.get("role_required", "") or _role_req
                            except Exception:
                                _ws = "mw-dev-chat"
                        self.db.wait_for.register(agent_id, {
                            "type": "task_for_role",
                            "workspace_id": _ws or "mw-dev-chat",
                            "role": _role_req or "",
                            "team_id": -1})
            except Exception:
                pass
            if agent:
                try:
                    agent.dehydrate()
                except Exception:
                    pass
            # Fermer la connexion SQLite DÉDIÉE du thread : sans ça chaque run
            # laisse des fds agents.db/modelweaver.db/catalogue.db ouverts à
            # vie (des milliers accumulés → contention + CPU).
            if thread_db is not None:
                try:
                    thread_db.close()
                except Exception:
                    pass
            with _LIVE_LOCK:
                _LIVE_AGENT_THREADS.pop(agent_id, None)

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
                (t["workspace_id"], t["team_id"], t["task_type"])
                for t in wdb.conn.execute(
                    "SELECT workspace_id, team_id, task_type FROM tasks "
                    "WHERE status = 'todo' AND task_type != '' "
                    "AND COALESCE(cancelled, 0) = 0"
                ).fetchall()}
            # Tokens à l'étape suivante (code_review, merger_code...) : les
            # agents capables du task_type doivent être réveillés.
            next_tokens = {
                (t["workspace_id"], t["team_id"])
                for t in wdb.conn.execute(
                    "SELECT workspace_id, team_id FROM tasks "
                    "WHERE status = 'todo' AND task_type IN "
                    "('code_review','merger_code','testing_code','merge_split') "
                    "AND COALESCE(cancelled, 0) = 0"
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
            next_tokens = set()
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
                # Le REVIEWER est réveillé par les tokens à relire (code_review) :
                # un token coding transitionne en code_review quand livré.
                if role == "reviewer":
                    return any(t == team or t == -1
                               for w, t in next_tokens)
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

        # Vraie activité : threads greedy vivants (réels) + agent_runtime.
        # C'est cette mesure qui pilote MIN_ACTIVE_TARGET — sinon on réveille
        # sans cesse des agents qui tournent déjà en thread (sous-comptés par
        # agent_runtime seul).
        active = max(len(self.list_active()), _live_agent_threads_count())
        count = 0
        # Les wait_for `ready` dont l'agent a TERMINÉ son run (thread fini,
        # plus dans agent_runtime ni _LIVE_AGENT_THREADS) sont re-réveillables :
        # l'agent a fini de travailler mais est resté en statut `ready` sans se
        # re-enregistrer en `waiting` (re-pioche épuisée, échec, ou fin simple).
        # Sans ça ils restent bloqués en `ready` pour toujours alors que du
        # travail existe (observé : tous les greedy dev-chat coincés `ready`).
        ready_rows = self.db.conn.execute("""
            SELECT w.id AS wid, w.agent_id, w.condition, w.status
            FROM wait_for w
            WHERE w.status = 'ready'
              AND w.agent_id NOT IN (SELECT agent_id FROM agent_runtime)
              AND w.ready_at < datetime('now', '-20 seconds')
            ORDER BY w.id ASC
            LIMIT 40
        """).fetchall()
        for r in ready_rows:
            if active + count >= MIN_ACTIVE_TARGET:
                break
            if _agent_thread_alive(r["agent_id"]):
                continue
            try:
                cond = _json.loads(r["condition"])
            except Exception:
                self.db.wait_for.mark_done(r["agent_id"])
                continue
            if not _cond_matches(cond):
                continue
            # Garde pause : une team en pause ne re-réveille pas ses greedy.
            try:
                _aname = self.db.conn.execute(
                    "SELECT name FROM agents WHERE agent_id = ?",
                    (r["agent_id"],)).fetchone()
                if _aname and self._agent_paused(_aname["name"]):
                    continue
            except Exception:
                pass
            # Re-réveiller : repasser en waiting pour que la boucle ci-dessous
            # le réveille (ou le réveiller directement via l'amorce).
            self.db.wait_for.mark_done(r["agent_id"])
            ws = cond.get("workspace_id", "")
            role = cond.get("role", "")
            team = int(cond.get("team_id", -1))
            if role == "reviewer":
                for _w, _t in next_tokens:
                    if _t == team or _t == -1:
                        ws = _w
                        break
            elif ws and not any(_r == role for _w, _t, _r in pending_tasks):
                ws = next(iter({w for w, _, _ in pending_tasks}), ws)
            threading.Thread(target=self._run_sleeping_agent,
                             args=(r["agent_id"], "wakeup: task pending", ws),
                             daemon=True).start()
            count += 1

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
            # ── Garde pause : une team en pause ne réveille pas ses greedy.
            try:
                _aname = self.db.conn.execute(
                    "SELECT name FROM agents WHERE agent_id = ?",
                    (w["agent_id"],)).fetchone()
                if _aname and self._agent_paused(_aname["name"]):
                    continue
            except Exception:
                pass
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
                if _role == "reviewer":
                    # Reviewer : passer le workspace où il y a des tokens à
                    # relire (code_review).
                    for _w, _t in next_tokens:
                        if _t == _team or _t == -1:
                            ws = _w
                            break
                else:
                    for _w, _t, _r in pending_tasks:
                        if _r == _role and (_t == _team or _t == -1):
                            ws = _w
                            break
            self.db.wait_for.mark_ready(w["id"])
            if _agent_thread_alive(w["agent_id"]):
                continue
            threading.Thread(target=self._run_sleeping_agent,
                             args=(w["agent_id"], req, ws),
                             daemon=True).start()
            count += 1

        # AMORCE : si aucun agent n'est encore en wait_for (premier cycle) mais
        # qu'il y a du travail et des agents greedy non hydratés, on les réveille
        # pour lancer le swarm. Le wait_for prend le relais ensuite (re-endormir).
        # CIBLE D'ACTIFS : on réveille des greedy jusqu'à atteindre au moins
        # MIN_ACTIVE_TARGET actifs simultanés (le wait_for seul n'en lance qu'un
        # par condition, le swarm resterait à 1-3 agents). Tant que des tâches
        # du rôle sont dispo, on complète le pool d'actifs.
        if active + count < MIN_ACTIVE_TARGET and (open_issues or pending_tasks):
            # PRIORITÉ REVIEWER : si des tokens sont à relire (code_review), les
            # reviewers passent AVANT les codeurs — sinon le backlog review
            # grossit sans fin (les codeurs produisent plus vite que 2-3
            # reviewers ne valident). Les reviewers en premier, puis le reste.
            _reviewer_first = "1" if next_tokens else "0"
            rows = self.db.conn.execute(f"""
                SELECT agent_id, name, role_type FROM agents
                WHERE (config_json LIKE '%"pick"%'
                       OR config_json LIKE '%claim_next%'
                       OR config_json LIKE '%Boucle gloutonne%'
                       OR config_json LIKE '%greedy%'
                       OR occupation = 'continue')
                  AND agent_id NOT IN (SELECT agent_id FROM agent_runtime)
                  AND agent_id NOT IN (SELECT agent_id FROM wait_for WHERE status='waiting')
                ORDER BY CASE WHEN role_type = 'relecteur' THEN {_reviewer_first} ELSE 0 END DESC
                LIMIT 40
            """).fetchall()
            # Cible d'actifs DYNAMIQUE : si un backlog review existe, garantir
            # au moins MIN_REVIEWERS_ACTIVE reviewers actifs — on AUGMENTE le
            # plafond au-delà de MIN_ACTIVE_TARGET pour que les reviewers aient
            # toujours des slots (les codeurs/autres greedy ne les évincent pas).
            _target = MIN_ACTIVE_TARGET
            if next_tokens:
                _rev_run = self.db.conn.execute(
                    "SELECT COUNT(*) n FROM agents WHERE role_type='relecteur' "
                    "AND status='RUNNING'").fetchone()
                _rev_run = _rev_run["n"] if _rev_run else 0
                if _rev_run < MIN_REVIEWERS_ACTIVE:
                    _target = MIN_ACTIVE_TARGET + (MIN_REVIEWERS_ACTIVE - _rev_run)
            for row in rows:
                if active + count >= _target:
                    break
                rt = ROLE_TO_TASK.get(row["role_type"], "")
                # analyste → issue ; rôles greedy → SEULEMENT si des tasks de son
                # rôle (+ team/projet) sont dispo, sinon il re-pioche rien et
                # spam les wait_for.
                if open_issues and rt == "analyst":
                    if _agent_thread_alive(row["agent_id"]):
                        continue
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
                        if _agent_thread_alive(row["agent_id"]):
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
                    # Le reviewer (relecteur) est réveillé si des tokens sont à
                    # relire (code_review).
                    if rt == "reviewer" and next_tokens:
                        pass  # réveiller le reviewer
                    elif not any(
                        _r in _compatible_roles(rt) or _r == rt
                        for _w, _t, _r in pending_tasks):
                        continue
                    if _agent_thread_alive(row["agent_id"]):
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
        id_proprietaire: Optional[str] = None,
        id_team: Optional[int] = None,
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
                                    resources_json, variables_json,
                                    id_proprietaire, id_team)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, ref, role, occupation,
                  json.dumps(config or {}), json.dumps(resources or {}), "{}",
                  id_proprietaire, id_team))
            self.db.conn.commit()
            agent_id = self.db.conn.execute(
                "SELECT agent_id FROM agents WHERE name = ?", (name,)
            ).fetchone()[0]
            # V0.6.8 : espace disque proprio
            from AgentFrameWork.agent_storage import AgentStorage
            AgentStorage(agent_id, self.db.conn).ensure()
            # Autorisations par défaut : privilèges member (level 1000) dans
            # le catalogue local (home + workspace). Best-effort.
            try:
                from services.catalogue_privileges_defaults import grant_agent
                grant_agent(agent_id, team_id=id_team)
            except Exception:
                pass
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

    def _log(msg: str):
        try:
            with open(Path(mw_home()) / "logs" / "agent-manager-tick.log", "a",
                      encoding="utf-8") as fh:
                fh.write(f"[{time.time():.0f}] {msg}\n")
        except Exception:
            pass

    manager = AgentManager()
    while True:
        try:
            result = manager.tick()
            if result["zombies_found"] > 0:
                print(json.dumps(result), flush=True)
            _log(json.dumps({k: result.get(k) for k in
                             ("active_agents", "woken_agents", "woken_tasks",
                              "woken_coordinator", "tasks_reclaimed")}
                            | {"live_threads": _live_agent_threads_count()}))
        except Exception as e:
            import traceback
            _log(f"TICK ERROR: {e}\n{traceback.format_exc()}")
            print(json.dumps({"error": str(e)}), flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    run_service()
