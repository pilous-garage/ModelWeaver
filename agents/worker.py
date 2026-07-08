"""Worker — Exécute une tâche LLM pour un agent.

Cycle d'exécution :
1. Hydrate l'agent (charge config, messages)
2. Construit le prompt (system + historique + requête)
3. Appelle le LLM via HTTP direct vers le endpoint du provider
4. Sauvegarde la réponse en BDD
5. Planifie la prochaine étape si nécessaire
6. Retourne le résultat
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

            result = self._call_llm(agent, provider, task, history, payload)

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

    def _call_llm(
        self,
        agent: Dict[str, Any],
        provider: Dict[str, Any],
        task: Dict[str, Any],
        history: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Appelle le LLM via HTTP direct et retourne {'content': ..., 'tokens_used': ..., 'summary': ...}."""
        messages = self._build_messages(agent, history, payload)

        endpoint = provider.get("endpoint_url")
        if not endpoint:
            return {"status": "no_endpoint", "content": "[Worker: pas d'endpoint_url configuré]", "tokens_used": 0, "summary": ""}

        api_key = None
        if provider.get("api_key_ref"):
            api_key = self.load_api_key(provider["api_key_ref"])

        return self._http_completion(endpoint, provider["model_name"], messages, api_key)

    def _http_completion(
        self,
        endpoint: str,
        model: str,
        messages: List[Dict[str, str]],
        api_key: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """Appel HTTP POST en format OpenAI /chat/completions.

        Compatible avec tous les providers implémentant le standard OpenAI
        (Groq, OpenRouter, Ollama, LiteLLM, etc.).
        """
        chat_url = endpoint.rstrip("/")
        if not chat_url.endswith("/chat/completions"):
            chat_url += "/chat/completions"

        body = json.dumps({
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "ModelWeaver/1.0",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        req = urllib.request.Request(chat_url, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {e.code} sur {model}: {error_body[:500]}")

        content = ""
        tokens_used = 0
        try:
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            tokens_used = usage.get("total_tokens", 0)
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Format de réponse inattendu: {json.dumps(data, indent=2)[:500]}")

        summary = content.strip()[:200]
        return {"status": "ok", "content": content, "tokens_used": tokens_used, "summary": summary}

    def _build_messages(
        self,
        agent: Dict[str, Any],
        history: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """Construit la liste de messages au format OpenAI."""
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
