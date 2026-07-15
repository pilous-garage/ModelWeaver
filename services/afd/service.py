#!/usr/bin/env python3
"""Agent Framework Daemon — Processus dédié pour l'exécution des agents.

Auto-régénération : au démarrage, scanne la table agents (sync()) pour
nettoyer les zombies et découvrir les agents à reprendre.

Communication :
  - Socket Unix pour les commandes (request/response via AgentDaemon.call())
  - StreamBus SQLite WAL pour le streaming cross-process

Usage:
    python services/afd/service.py serve
"""

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._common import mw_home, log_to_file
from AgentFrameWork.stream_bus import activate_cross_process, resolve_stream_path
from AgentFrameWork.ticker import AsyncTicker
from services.afd.ipc import AFDSocketServer
from services.agent_daemon import AgentDaemon

AFD_VERSION = "0.1.0"


def _ensure_dir() -> Path:
    d = mw_home()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Handler de requêtes IPC ──

def _handle_ipc_request(req: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatche une requête JSON reçue via socket Unix vers AgentDaemon."""
    req_id = req.get("id")
    method = req.get("call")
    if not method:
        return {"ok": False, "error": "missing 'call' field", "id": req_id}

    try:
        if method == "ping":
            return {"ok": True, "result": {"pong": True}, "id": req_id}

        if method == "call":
            agent_ref = req.get("agent_ref")
            function = req.get("function")
            if not function:
                return {"ok": False, "error": "missing function", "id": req_id}
            args = req.get("args", {})
            result = AgentDaemon.call(agent_ref, function, **args)
            return {"ok": result.get("status") in ("ok", "success"),
                    "result": result, "id": req_id}

        if method == "sync":
            result = AgentDaemon.sync()
            return {"ok": True, "result": result, "id": req_id}

        if method == "capabilities":
            result = AgentDaemon.capabilities_catalog()
            return {"ok": True, "result": result, "id": req_id}

        return {"ok": False, "error": f"unknown method: {method}", "id": req_id}
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e),
                "trace": traceback.format_exc(), "id": req_id}


# ── Boucle Ticker (asyncio en thread dédié) ──

def _run_ticker(poll_interval: float = 5.0):
    """Lance l'AsyncTicker dans un thread dédié (event loop asyncio)."""
    import asyncio
    from services.agent_manager.service import AgentManager

    async def _loop():
        ticker = AsyncTicker(
            agent_manager=AgentManager(),
            poll_interval=poll_interval,
        )
        await ticker.start()

    def _thread():
        asyncio.run(_loop())

    t = threading.Thread(target=_thread, daemon=True, name="afd-ticker")
    t.start()
    return t


# ── Main ──

def serve(args):
    """Point d'entrée de l'AFD : démarre le socket Unix + Ticker + sync."""
    _ensure_dir()

    # Auto-régénération : synchro avec la table agents
    sync_result = AgentDaemon.sync()
    print(f"AFD sync : {sync_result['zombies_cleaned']} zombies nettoyés, "
          f"{len(sync_result['agents_active_on_disk'])} agents actifs sur disque")

    # Activation du StreamBus cross-process (SQLite WAL tmpfs)
    stream_path = resolve_stream_path()
    activate_cross_process(stream_path)
    print(f"StreamBus cross-process activé : {stream_path}")

    # Démarrage du Ticker (surveillance heartbeats/zombies)
    _run_ticker(poll_interval=getattr(args, 'poll', 5.0))
    print(f"Ticker démarré (poll={getattr(args, 'poll', 5.0)}s)")

    # Socket Unix IPC
    server = AFDSocketServer()
    sock_path = server.path
    print(f"AFD v{AFD_VERSION} — écoute sur {sock_path}")
    print(f"  → gateway: connecte-toi à {sock_path} pour proxy agents")

    # Écrire le chemin du socket pour le gateway
    afd_info = mw_home() / "afd.info"
    afd_info.write_text(json.dumps({"socket": sock_path, "version": AFD_VERSION}))

    log_to_file("afd", f"STARTED v{AFD_VERSION} sock={sock_path} stream={stream_path}")

    try:
        server.serve(_handle_ipc_request)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        log_to_file("afd", f"STOPPED v{AFD_VERSION}")
        print("AFD arrêté.")


def main():
    parser = argparse.ArgumentParser(description="Agent Framework Daemon")
    sub = parser.add_subparsers(dest="cmd")
    sp = sub.add_parser("serve", help="Démarrer l'AFD")
    sp.add_argument("--poll", type=float, default=5.0, help="Intervalle Ticker (secondes)")
    args = parser.parse_args()
    if args.cmd == "serve":
        serve(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()