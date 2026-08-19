"""system_registry — enregistrement des services système + état runtime.

Appelé au boot du daemon (serve) : déclare les services ModelWeaver dans
services.db (daemon, supervisor_general, ticker_general, watcher, sqlite,
équipes…) et pose l'état runtime global (runtime_state) + pid/port des
services du process.

Les services à tick (file_watcher, petri_runtime, usage_tick…) sont
déclarés par le ServiceTicker au register — pas besoin ici.
"""

from __future__ import annotations

from typing import Any, Dict

from modules.sqlite.paths import mw_home


# name → (kind, module, description)
SYSTEM_SERVICES = [
    ("daemon", "daemon", "services.api.daemon",
     "serveur HTTP + boot de tous les services"),
    ("supervisor_general", "supervisor_general", "services.service_manager",
     "supervision des services (service_commands, relances)"),
    ("team_manager", "supervisor_general", "services.team_manager",
     "supervision de la santé des équipes"),
    ("ticker_general", "ticker_general", "services.service_ticker",
     "ticker maître (threads éphémères + permanents 1s)"),
    ("watcher", "watch", "services.watcher",
     "détection/action des problèmes swarm (P1-P10)"),
    ("sqlite", "sqlite", "modules.sqlite",
     "moteur de données : tous les domaines .db"),
    ("runtime_llm", "sqlite", "modules.sqlite.runtime_llm",
     "domaine de bridge LLM (adresses, usage, allocation)"),
]


def register_system_services(pid: int = 0, port: int = 0,
                             mw_version: str = "",
                             log=None) -> Dict[str, Any]:
    """Déclare la liste SYSTEM_SERVICES + état runtime global. Idempotent."""
    from modules.sqlite.services import db as sdb
    from modules.sqlite.services import write as SW
    from modules.sqlite.runtime import db as rdb
    from modules.sqlite.runtime import write as RW

    d = sdb()
    regs = []
    for name, kind, module, desc in SYSTEM_SERVICES:
        SW.register_service(d, name, kind=kind, module=module, description=desc)
        regs.append(name)
        SW.set_service_status(d, name, "running", pid=pid,
                              port=port if name == "daemon" else 0)
    d.close()

    rd = rdb()
    RW.set_state(rd, pid=pid, host=_hostname(), mw_version=mw_version,
                 boot_at=_now(), mw_home=str(mw_home()),
                 python_version=_py())
    rd.close()
    if log:
        log.info("system_registry: %d services déclarés + runtime_state",
                 len(regs))
    return {"ok": True, "services": regs}


def touch_service_state(name: str, status: str,
                        pid: int = 0, port: int = 0,
                        last_tick: float = 0, next_tick: float = 0) -> None:
    """Mise à jour fine d'un service (utilisé par le daemon en marche)."""
    from modules.sqlite.runtime import db as rdb
    from modules.sqlite.runtime import write as RW
    rd = rdb()
    RW.touch(rd, name, pid=pid, port=port, status=status,
             last_tick=last_tick, next_tick=next_tick)
    rd.close()


def _hostname() -> str:
    import socket
    try:
        return socket.gethostname()
    except Exception:
        return ""


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _py() -> str:
    import sys
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"