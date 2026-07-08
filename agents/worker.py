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
from typing import Any, Dict, List, Optional

from sql.agent_repository import (
    AgentMessageRepository, AgentRepository,
    ModelProviderRepository, SessionRepository, WakeupCallRepository,
)
from agents.pipeline_executor import PipelineExecutor
from agents.dsl_executor import DSLExecutor


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

    def load_api_key(self, key_ref: str) -> Optional[str]:
        if not key_ref or not self.api_keys_repo:
            return None
        key_data = self.api_keys_repo.get(key_ref)
        return key_data["key_value"] if key_data else None

    def execute(self, task_id: int) -> Dict[str, Any]:
        """Exécute une tâche et retourne le résultat."""
        task = self.wakeup_calls.get(task_id)
        if not task:
            return {"status": "error", "message": f"Task {task_id} not found"}

        agent = self.agents.get(task["agent_id"])
        if not agent:
            self.wakeup_calls.fail(task_id, "Agent introuvable")
            return {"status": "error", "message": "Agent introuvable"}

        session = self.sessions.get(task["session_id"])
        if not session:
            self.wakeup_calls.fail(task_id, "Session introuvable")
            return {"status": "error", "message": "Session introuvable"}

        provider = self.model_providers.get(agent["provider_id"]) if agent["provider_id"] else None
        if not provider:
            self.wakeup_calls.fail(task_id, "Aucun provider assigné")
            return {"status": "error", "message": "Aucun provider assigné"}

        try:
            self.agents.update_status(agent["agent_id"], "BUSY")
            if self.db_conn:
                self.db_conn.commit()

            history = self.messages.list_by_session(task["session_id"])
            payload = json.loads(task["request_payload"]) if task["request_payload"] else {}

            # --- CHOIX : WORKFLOW DSL vs APPEL SIMPLE ---
            config = json.loads(agent["config_json"]) if agent.get("config_json") else {}
            workflow = config.get("pipeline")

            if workflow:
                result = self._execute_workflow(agent, provider, task, history, payload, workflow)
            else:
                result = self._call_llm_simple(agent, provider, task, history, payload)

            # Gestion du résultat du workflow
            if result["status"] == "sleeping":
                self.wakeup_calls.update_sleep(task_id, result["sleep_seconds"], result["next_step_id"])
                self.agents.update_status(agent["agent_id"], "IDLE")
                return result

            if result["status"] == "terminated":
                self.agents.terminate(agent["agent_id"], successor_id=result.get("successor_id"))
                self.wakeup_calls.complete(task_id, "Agent terminé / Successeur activé")
                return result

            # Sauvegarde du message final si succès
            if result["status"] == "ok" and "content" in result:
                self.messages.add(
                    session_id=task["session_id"],
                    role="assistant",
                    content=result["content"],
                    tokens_used=result.get("tokens_used", 0),
                )
                self.wakeup_calls.complete(task_id, result.get("summary", result["content"][:200]))
            
            self.agents.update_status(agent["agent_id"], "IDLE")
            return result

        except Exception as e:
            self.wakeup_calls.fail(task_id, str(e))
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
                self.load_api_key(provider.get("api_key_ref")),
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
                                     (agent["agent_id"], json.dumps({"type": "succession_request", "reason": reason, "variables": variables})))
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

        api_key = self.load_api_key(provider.get("api_key_ref"))
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
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code} sur {model}: {error_body[:500]}")

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {"status": "ok", "content": content, "tokens_used": usage.get("total_tokens", 0), "summary": content.strip()[:200]}

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
