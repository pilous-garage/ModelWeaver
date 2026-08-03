"""Client du service LLM Manager — allocation et état via socket Unix.

Usage (depuis n'importe quel process, ex. un agent) :
    from services.llm_manager.client import get_llm_client
    llm = get_llm_client()
    llm.allocate({...})          # choisit un provider/modèle
    llm.report_failure(provider, model)   # signale un échec
"""

from typing import Any, Dict, Optional

from services.afd.ipc import AFDSocketClient
from services._common import mw_home


def llm_socket_path() -> str:
    return str(mw_home() / "llm.sock")


class LLMClient:
    def __init__(self, sock_path: Optional[str] = None, timeout: float = 90.0):
        # Timeout généreux : une allocation probe réellement les candidats
        # (jusqu'à PROBE_TRIES appels, chacun avec un timeout de chat 120s) —
        # 30s était trop court et faisait échouer l'allocation en plein run.
        self._client = AFDSocketClient(sock_path or llm_socket_path(),
                                       timeout=timeout)

    def ping(self) -> bool:
        resp = self._client.send({"call": "ping", "id": "gw-llm-ping"})
        return bool(resp.get("ok"))

    def allocate(self, params: dict) -> Dict[str, Any]:
        resp = self._client.send({"call": "allocate", "params": params,
                                  "id": "gw-llm-alloc"})
        if not resp.get("ok"):
            return {"status": "error",
                    "error": resp.get("error", "llm_service_erreur")}
        return resp.get("result", {})

    def state(self) -> Dict[str, Any]:
        resp = self._client.send({"call": "state", "id": "gw-llm-state"})
        if not resp.get("ok"):
            return {"status": "error",
                    "error": resp.get("error", "llm_service_erreur")}
        return resp.get("result", {})

    def report_failure(self, provider_ref: str, model_ref: str) -> Dict[str, Any]:
        resp = self._client.send({"call": "report_failure",
                                  "params": {"provider_ref": provider_ref,
                                             "model_ref": model_ref},
                                  "id": "gw-llm-fail"})
        if not resp.get("ok"):
            return {"status": "error",
                    "error": resp.get("error", "llm_service_erreur")}
        return resp.get("result", {})

    def report_ok(self, provider_ref: str, model_ref: str) -> Dict[str, Any]:
        resp = self._client.send({"call": "report_ok",
                                  "params": {"provider_ref": provider_ref,
                                             "model_ref": model_ref},
                                  "id": "gw-llm-ok"})
        if not resp.get("ok"):
            return {"status": "error",
                    "error": resp.get("error", "llm_service_erreur")}
        return resp.get("result", {})

    def report_end(self, agent_id: str) -> Dict[str, Any]:
        """Libère les claims de modèles de cet agent (fin de tâche)."""
        resp = self._client.send({"call": "report_end",
                                  "params": {"agent_id": agent_id},
                                  "id": "gw-llm-end"})
        if not resp.get("ok"):
            return {"status": "error",
                    "error": resp.get("error", "llm_service_erreur")}
        return resp.get("result", {})


_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
