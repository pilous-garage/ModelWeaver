"""Bridge natif OpenAI — utilise le SDK ``openai`` au lieu de LiteLLM.

Exemple de bridge natif pour le registry ``PROVIDER_BRIDGES``.
Ne nécessite pas litellm pour les appels OpenAI, ce qui réduit la
surface de dépendance et les temps d'import.

Le registry (BridgeRegistry) charge ce module uniquement quand un
provider demande "openai". Les autres providers continuent d'utiliser
le fallback LiteLLM.

Respecte le contrat BaseBridge (vérifié par hardcheck).
"""

import logging
import os
from typing import Any, Dict, Iterator, List, Optional

from modules.llm_manager.base_bridge import (
    BaseBridge, ChatResponse, ModelCapabilities,
    ErrorCategory, BridgeError,
)

logger = logging.getLogger("modelweaver.bridge.openai")


class Bridge(BaseBridge):
    """Bridge SDK OpenAI natif.

    Ignore ``provider_ref`` (toujours "openai") et utilise directement
    le SDK ``openai`` avec la clé résolue par le KeyManager.

    Les providers OpenAI-compatible (api_base personnalisé) restent sur
    LiteLLMBridgeDefunct — ce bridge est réservé à l'API OpenAI officielle.
    """

    def __init__(self, cat=None, km=None):
        self.cat = cat
        self.km = km
        self._client = None

    def _lazy_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai SDK not installed: pip install openai")

        api_key = None
        api_base = None
        if self.km:
            try:
                rec = self.km.get_key("openai")
                if rec and isinstance(rec, dict):
                    api_key = rec.get("api_key")
                    api_base = rec.get("api_base")
            except Exception:
                pass

        self._client = OpenAI(
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=api_base,
        )
        return self._client

    def chat(self, provider_ref: str, model_ref: str,
             messages: List[Dict[str, str]],
             temperature: float = 0.7,
             max_tokens: Optional[int] = None,
             system_prompt: Optional[str] = None,
             stream: bool = False,
             **params) -> ChatResponse:
        client = self._lazy_client()
        msgs = list(messages)
        if system_prompt and not any(m.get("role") == "system" for m in msgs):
            msgs.insert(0, {"role": "system", "content": system_prompt})
        kwargs = dict(model=model_ref, messages=msgs, temperature=temperature)
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        kwargs.update(params)

        try:
            response = client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            return ChatResponse(
                content=choice.message.content or "",
                model=response.model or model_ref,
                finish_reason=choice.finish_reason or "stop",
                usage={
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                } if response.usage else {},
            )
        except Exception as exc:
            raise self.classify_error(exc, provider_ref, model_ref)

    def chat_stream(self, provider_ref: str, model_ref: str,
                    messages: List[Dict[str, str]],
                    temperature: float = 0.7,
                    max_tokens: Optional[int] = None,
                    system_prompt: Optional[str] = None,
                    **params) -> Iterator[str]:
        client = self._lazy_client()
        msgs = list(messages)
        if system_prompt and not any(m.get("role") == "system" for m in msgs):
            msgs.insert(0, {"role": "system", "content": system_prompt})
        kwargs = dict(model=model_ref, messages=msgs, temperature=temperature, stream=True)
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        kwargs.update(params)

        try:
            stream_resp = client.chat.completions.create(**kwargs)
            for chunk in stream_resp:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield delta.content
        except Exception as exc:
            raise self.classify_error(exc, provider_ref, model_ref)

    def get_capabilities(self, provider_ref: str, model_ref: str) -> ModelCapabilities:
        from modules.llm_manager.llm_manager import LLMManager
        fallback = LLMManager(self.cat, km=self.km).get_bridge()
        return fallback.get_capabilities(provider_ref, model_ref)

    def list_available_providers(self) -> List[Dict[str, Any]]:
        return [{"ref": "openai", "name": "OpenAI", "bridge": "openai_native"}]

    def list_available_models(self, provider_ref: str) -> List[Dict[str, Any]]:
        client = self._lazy_client()
        try:
            models = client.models.list()
            return [{"ref": m.id, "name": m.id} for m in models]
        except Exception:
            return []

    def health_check(self, provider_ref: Optional[str] = None) -> Dict[str, Any]:
        if provider_ref and provider_ref != "openai":
            return {"bridge": "openai_native", "provider": provider_ref, "status": "skipped"}
        try:
            self._lazy_client()
            return {"bridge": "openai_native", "status": "ok"}
        except Exception as exc:
            return {"bridge": "openai_native", "status": "error", "error": str(exc)}

    def classify_error(self, error: Any,
                       provider_ref: str = "",
                       model_ref: str = "") -> BridgeError:
        msg = str(error).lower()
        if any(k in msg for k in ("auth", "401", "403", "api key", "invalid key")):
            return BridgeError(ErrorCategory.AUTH, str(error), provider_ref, model_ref)
        if any(k in msg for k in ("rate limit", "429", "too many requests")):
            return BridgeError(ErrorCategory.RATE_LIMIT, str(error), provider_ref, model_ref)
        if any(k in msg for k in ("context length", "max context", "too many tokens")):
            return BridgeError(ErrorCategory.CONTEXT, str(error), provider_ref, model_ref)
        if any(k in msg for k in ("timeout", "connection")):
            return BridgeError(ErrorCategory.TIMEOUT, str(error), provider_ref, model_ref)
        if any(k in msg for k in ("500", "502", "503")):
            return BridgeError(ErrorCategory.SERVER, str(error), provider_ref, model_ref)
        return BridgeError(ErrorCategory.UNKNOWN, str(error), provider_ref, model_ref)
