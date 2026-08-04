"""Bridge minimal pour tests et intégration pause."""

from typing import Dict, List, Optional


class ChatMessage:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content


class BridgeResponse:
    def __init__(self, content: str = "", provider_used: str = "", model_used: str = "", usage: Optional[Dict] = None, budget: Optional[Dict] = None):
        self.content = content
        self.provider_used = provider_used
        self.model_used = model_used
        self.usage = usage or {}
        self.budget = budget or {}
        self.tool_calls = None


class BridgeError(Exception):
    pass


class Bridge:
    """Bridge LLM minimal pour tests."""

    def __init__(self, stream_delay: float = 0.01):
        self.stream_delay = stream_delay
        self.calls: List[Dict] = []

    def chat(self, provider_ref: str, model_ref: str, messages: List[Dict], temperature: float = 0.7, max_tokens: int = 4096, agent_id: Optional[str] = None, **kwargs) -> BridgeResponse:
        self.calls.append({"provider": provider_ref, "model": model_ref, "messages": messages, "agent_id": agent_id})
        content = "Réponse statique"
        return BridgeResponse(content=content, provider_used=provider_ref, model_used=model_ref, usage={"total_tokens": len(content)})

    def chat_stream(self, provider_ref: str, model_ref: str, messages: List[Dict], temperature: float = 0.7, max_tokens: int = 4096, agent_id: Optional[str] = None, **kwargs):
        self.calls.append({"provider": provider_ref, "model": model_ref, "messages": messages, "agent_id": agent_id, "stream": True})
        text = "Réponse streamée"
        for ch in text:
            yield ch
