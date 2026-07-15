"""Client AFD pour le gateway REST — proxy les appels agents via socket Unix.

Usage:
    from services.api.afd_client import get_afd_client, AFDProxy

    # Proxy automatique : si l'AFD est joignable → socket Unix, sinon → local
    proxy = get_afd_client()
    result = proxy.call(agent_ref, function, **kwargs)
"""

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from services._common import mw_home
from services.afd.ipc import AFDSocketClient


class AFDProxy:
    """Proxy vers l'AFD : essaie le socket Unix, fallback local AgentDaemon."""

    def __init__(self):
        self._client: Optional[AFDSocketClient] = None
        self._available: Optional[bool] = None
        self._lock = threading.Lock()

    def _check_available(self) -> bool:
        with self._lock:
            if self._available is not None:
                return self._available
            sock_path = str(mw_home() / "afd.sock")
            if not Path(sock_path).exists():
                self._available = False
                return False
            self._client = AFDSocketClient(sock_path)
            ping = self._client.send({"call": "ping", "id": "probe"})
            self._available = ping.get("ok", False)
            return self._available

    def call(self, agent_ref, function: str, **kwargs) -> Dict[str, Any]:
        """Appelle l'AFD si disponible, sinon fallback local AgentDaemon."""
        if self._check_available():
            resp = self._client.send({
                "call": "call",
                "agent_ref": agent_ref,
                "function": function,
                "args": kwargs,
                "id": f"gw-{agent_ref}-{function}",
            })
            if resp.get("ok"):
                return resp.get("result", {})
            # AFD a refusé — retourner l'erreur telle quelle
            result = resp.get("result", {})
            if result and "status" in result:
                return result
            return {"status": "error", "error": resp.get("error", "afd_error")}

        # Fallback local (AFD pas lancé → mode mono-process)
        from services.agent_daemon import AgentDaemon
        return AgentDaemon.call(agent_ref, function, **kwargs)

    def _local_routes_for(self, agent_id: int) -> list:
        """Routes introspection (toujours local, pas de proxy AFD)."""
        from services.agent_daemon import AgentDaemon
        return AgentDaemon.routes_for(agent_id)


# Singleton partagé (réutilise la connexion)
_afd_proxy: Optional[AFDProxy] = None
_afd_lock = threading.Lock()


def get_afd_client() -> AFDProxy:
    global _afd_proxy
    with _afd_lock:
        if _afd_proxy is None:
            _afd_proxy = AFDProxy()
        return _afd_proxy