"""Client du superviseur — interroge le service supervisor via socket Unix.

Usage (depuis n'importe quel process, ex. le daemon) :
    from services.supervisor.client import get_supervisor_client
    client = get_supervisor_client()
    client.status()       # liste des services + PID + sockets
    client.list_services()
    client.restart("api")
    client.shutdown()
"""

from pathlib import Path
from typing import Any, Dict, Optional

from services.afd.ipc import AFDSocketClient
from services._common import mw_home


def supervisor_socket_path() -> str:
    return str(mw_home() / "supervisor.sock")


class SupervisorClient:
    def __init__(self, sock_path: Optional[str] = None, timeout: float = 10.0):
        self._client = AFDSocketClient(sock_path or supervisor_socket_path(),
                                       timeout=timeout)

    def _call(self, call: str, **kwargs) -> Dict[str, Any]:
        resp = self._client.send({"call": call, "id": f"gw-{call}", **kwargs})
        if not resp.get("ok"):
            return {"status": "error", "error": resp.get("error", "supervisor_erreur")}
        return resp.get("result", {})

    def ping(self) -> bool:
        resp = self._client.send({"call": "ping", "id": "gw-ping"})
        return bool(resp.get("ok"))

    def status(self) -> Dict[str, Any]:
        return self._call("status")

    def list_services(self) -> Dict[str, Any]:
        return self._call("list")

    def start(self, name: str) -> Dict[str, Any]:
        return self._call("start", name=name)

    def stop(self, name: str) -> Dict[str, Any]:
        return self._call("stop", name=name)

    def restart(self, name: str) -> Dict[str, Any]:
        return self._call("restart", name=name)

    def reset(self, name: str) -> Dict[str, Any]:
        return self._call("reset", name=name)

    def reset_all(self) -> Dict[str, Any]:
        return self._call("reset_all")

    def shutdown(self) -> Dict[str, Any]:
        return self._call("shutdown")

    def shutdown_all(self) -> Dict[str, Any]:
        return self._call("shutdown_all")


_supervisor_client: Optional[SupervisorClient] = None


def get_supervisor_client() -> SupervisorClient:
    global _supervisor_client
    if _supervisor_client is None:
        _supervisor_client = SupervisorClient()
    return _supervisor_client
