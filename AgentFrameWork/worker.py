"""Worker — Exécute une tâche LLM ou un workflow pour un agent.

Cycle d'exécution :
1. Hydrate l'agent (charge config, messages)
2. Si le rôle a un workflow : exécute le DSL via DSLExecutor
3. Sinon : effectue un appel LLM simple
4. Sauvegarde la réponse et gère le cycle de vie (IDLE, SLEEPING, TERMINATED)
5. Retourne le résultat
"""

import json
import traceback
import urllib.request
import urllib.error
import logging
import os
from typing import Any, Dict, List, Optional

from sql.agent_repository import (
    AgentMessageRepository, AgentRepository,
    ModelProviderRepository, SessionRepository, WakeupCallRepository,
)
import logging

logger = logging.getLogger("modelweaver.worker")

from AgentFrameWork.pipeline_executor import PipelineExecutor
from AgentFrameWork.dsl_executor import DSLExecutor
from AgentFrameWork.tool_executor import ToolExecutor


class Worker:
    """Exécute une wakeup_call pour un agent."""

    def __init__(
        self,
        agents: AgentRepository,
        model_providers: ModelProviderRepository,
        sessions: SessionRepository,
        messages: AgentMessageRepository,
        wakeup_calls: WakeupCallRepository,
        api_keys_repo=None,
        db_conn=None,
    ):
        self.agents = agents
        self.model_providers = model_providers
        self.sessions = sessions
        self.messages = messages
        self.wakeup_calls = wakeup_calls
        self.api_keys_repo = api_keys_repo
        self.db_conn = db_conn
        self.pipeline_executor = PipelineExecutor()
        # On initialise le ToolExecutor avec le root du projet
        self.tool_executor = ToolExecutor(workspace_root=os.getcwd())

    def load_api_key(self, model_provider_id: int) -> Optional[str]:
        if not model_provider_id or not self.api_keys_repo:
            return None
        
        # 1. On récupère le model_provider pour avoir l'ID du provider parent
        mp = self.model_providers.get(model_provider_id)
        if not mp:
            return None
            
        # 2. On récupère le provider pour avoir son ref
        provider = self.db_conn.execute(
            "SELECT ref FROM providers WHERE id = ?", (mp["provider_id"],)
        ).fetchone()
        
        if not provider:
            return None
            
        # 3. On utilise le ref pour trouver la clé
        key_data = self.api_keys_repo.get_for_provider(provider["ref"])
        return key_data["key_value"] if key_data else None


    def execute(self, task_id: int) -> Dict[str, Any]:
        """Exécute une tâche et retourne le résultat."""
        # 1. On regarde si c'est une wakeup_call classique ou une shared_task
        task = self.wakeup_calls.get(task_id)
        shared_task = None
        if task:
            payload = json.loads(task["request_payload"]) if task["request_payload"] else {}
            if "task_id" in payload:
                shared_task = self.db_conn.execute(
                    "SELECT * FROM shared_tasks WHERE task_id = ?", 
                    (payload["task_id"],)
                ).fetchone()
        else:
            # Si on n'a pas de wakeup_call, on check si c'est un ID de shared_task direct
            shared_task = self.db_conn.execute(
                "SELECT * FROM shared_tasks WHERE task_id = ?", 
                (task_id,)
            ).fetchone()

        if not task and not shared_task:
            return {"status": "error", "message": f"Task {task_id} not found"}

        agent = self.agents.get(task["agent_id"] if task else 0) # Need a way to get agent for shared task
        # Pour les shared_tasks, on doit savoir quel agent a été assigné
        if shared_task:
            agent = self.agents.get(shared_task["assigned_to"])

        if not agent:
            # On échoue la tâche si possible
            if shared_task:
                self.db_conn.execute("UPDATE shared_tasks SET status='FAILED' WHERE task_id=?", (shared_task["task_id"],))
            elif task:
                self.wakeup_calls.fail(task_id, "Agent introuvable")
            return {"status": "error", "message": "Agent introuvable"}

        session = self.sessions.get(task["session_id"] if task else None)
        if not session:
            # On crée une session si c'est une shared_task sans session associée
            session_id = self.db.sessions.create(agent["agent_id"], context_summary=f"Task {shared_task['title'] if shared_task else task_id}")
            session = self.sessions.get(session_id)
        else:
            session_id = session["session_id"]

        provider = self.model_providers.get(agent["provider_id"]) if agent["provider_id"] else None
        if not provider:
            if task: self.wakeup_calls.fail(task_id, "Aucun provider assigné")
            return {"status": "error", "message": "Aucun provider assigné"}

        try:
            self.agents.update_status(agent["agent_id"], "BUSY")
            if self.db_conn:
                self.db_conn.commit()

            history = self.messages.list_by_session(session["session_id"])
            
            # Construction du payload
            if shared_task:
                prompt = f"Tâche : {shared_task['title']}\nDescription : {shared_task['description']}\nContexte : {shared_task['context']}"
                payload = {"request": prompt}
            elif task:
                payload = json.loads(task["request_payload"]) if task["request_payload"] else {}
            else:
                payload = {}

            # --- CHOIX : WORKFLOW DSL vs APPEL SIMPLE ---
            config = json.loads(agent["config_json"]) if agent.get("config_json") else {}
            workflow = config.get("pipeline")

            if workflow:
                result = self._execute_workflow(agent, provider, task or {"task_id": shared_task["task_id"] if shared_task else None}, history, payload)
            else:
                result = self._call_llm_simple(agent, provider, task or {"task_id": shared_task["task_id"] if shared_task else None}, history, payload)

            # Gestion du résultat du workflow
            if result["status"] == "sleeping":
                # On utilise l'ID de la wakeup_call originale ou on en crée une nouvelle
                t_id = task["task_id"] if task else shared_task["task_id"]
                self.wakeup_calls.update_sleep(t_id, result["sleep_seconds"], result["next_step_id"])
                self.agents.update_status(agent["agent_id"], "IDLE")
                return result

            if result["status"] == "terminated":
                self.agents.terminate(agent["agent_id"], successor_id=result.get("successor_id"))
                if task: self.wakeup_calls.complete(task["task_id"], "Agent terminé / Successeur activé")
                return result

            # Sauvegarde du message final si succès
            if result["status"] == "ok" and "content" in result:
                self.messages.add(
                    session_id=session["session_id"],
                    role="assistant",
                    content=result["content"],
                    tokens_used=result.get("tokens_used", 0),
                )
                if task:
                    self.wakeup_calls.complete(task["task_id"], result.get("summary", result["content"][:200]))
                if shared_task:
                    self.db_conn.execute("UPDATE shared_tasks SET status='DONE' WHERE task_id=?", (shared_task["task_id"],))

            self.agents.update_status(agent["agent_id"], "IDLE")
            return result

        except Exception as e:
            logger.exception("Erreur lors de l'exécution de la tâche %d", task_id if task else "unknown")
            if task: self.wakeup_calls.fail(task_id, str(e))
            if shared_task: self.db_conn.execute("UPDATE shared_tasks SET status='FAILED' WHERE task_id=?", (shared_task["task_id"],))
            self.agents.update_status(agent["agent_id"], "IDLE")
            return {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

    def _execute_workflow(self, agent, provider, task, history, payload, workflow) -> Dict[str, Any]:
        """Exécute le workflow via le DSLExecutor."""
        
        def llm_wrapper(messages, variables, skill_prompt, temperature, max_tokens):
            if self.llm_callback:
                return self.llm_callback(messages, variables, skill_prompt, temperature, max_tokens)
            
            res = self._http_completion(
                provider["endpoint_url"], 
                provider["model_name"], 
                messages, 
                self.load_api_key(provider["provider_id"]),
                temperature or 0.7, 
                max_tokens or 4096
            )
            return res["content"]
        
        executor = DSLExecutor(
            pipeline_executor=self.pipeline_executor,
            llm_call_fn=llm_wrapper,
            save_state_fn=lambda state: self.agents.save_state(agent["agent_id"], state),
            signal_successor_fn=lambda reason, variables, sessions: 
                self.db_conn.execute("INSERT INTO agent_queue (from_agent_id, to_agent_id, content, message_type) VALUES (?, NULL, ?, 'notification')",
                                      (agent["agent_id"], json.dumps({"type": "succession_request", "reason": reason, "variables": variables}))),
            tool_call_fn=self.tool_executor.execute
        )
        
        current_state = self.agents.load_state(agent["agent_id"])
        res = executor.run(workflow, history, variables=current_state)
        self.agents.save_state(agent["agent_id"], res.variables)
        return res.to_dict()


    def _call_llm_simple(self, agent, provider, task, history, payload) -> Dict[str, Any]:
        """Ancien chemin : appel LLM unique."""
        messages = self._build_messages(agent, history, payload)
        endpoint = provider.get("endpoint_url")
        if not endpoint:
            return {"status": "no_endpoint", "content": "[Worker: pas d'endpoint_url configuré]", "tokens_used": 0, "summary": ""}

        api_key = self.load_api_key(provider["provider_id"])
        return self._http_completion(endpoint, provider["model_name"], messages, api_key)

    def _http_completion(self, endpoint, model, messages, api_key=None, temperature=0.7, max_tokens=4096) -> Dict[str, Any]:
        chat_url = endpoint.rstrip("/")
        if not chat_url.endswith("/chat/completions"):
            chat_url += "/chat/completions"

        body = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")

        headers = {"Content-Type": "application/json", "User-Agent": "ModelWeaver/1.0"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(chat_url, data=body, headers=headers, method="POST")
        
        # --- Robustness: Retry Logic ---
        max_retries = 3
        backoff = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    return {"status": "ok", "content": content, "tokens_used": usage.get("total_tokens", 0), "summary": content.strip()[:200]}
            except urllib.error.HTTPError as e:
                # Retry on 429 (Too Many Requests) or 5xx (Server Errors)
                if e.code == 429 or (500 <= e.code < 600):
                    if attempt < max_retries - 1:
                        import time
                        sleep_time = backoff ** (attempt + 1)
                        logger.warning("HTTP %d sur %s. Retry %d/%d dans %ds...", e.code, model, attempt+1, max_retries, sleep_time)
                        time.sleep(sleep_time)
                        continue
                
                error_body = e.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {e.code} sur {model}: {error_body[:500]}")
            except Exception as e:
                if attempt < max_retries - 1:
                    import time
                    time.sleep(backoff ** (attempt + 1))
                    continue
                raise e

    def _build_messages(self, agent, history, payload) -> List[Dict[str, str]]:
        messages = []
        config = json.loads(agent["config_json"]) if agent.get("config_json") else {}
        system_prompt = config.get("system_prompt", "Tu es un agent IA.")
        messages.append({"role": "system", "content": system_prompt})
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        user_content = payload.get("request", payload.get("prompt", ""))
        if user_content:
            messages.append({"role": "user", "content": user_content})
        return messages
