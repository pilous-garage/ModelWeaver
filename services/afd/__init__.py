"""Agent Framework Daemon — Processus dédié pour les agents.

Usage (AFD standalone):
    python services/afd/service.py serve

Usage (gateway proxy via IPC):
    from services.afd.ipc import AFDSocketClient
    client = AFDSocketClient()
    resp = client.send({"call": "call", "agent_ref": 42, "function": "chat", "args": {...}})
"""

from services.afd.ipc import AFDSocketServer, AFDSocketClient
from services.afd.service import serve

__all__ = ["AFDSocketServer", "AFDSocketClient", "serve"]