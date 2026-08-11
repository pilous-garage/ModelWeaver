#!/usr/bin/env python3
"""runtime_permissions — Borne l'accès DAEMON depuis les objets runtime.

Principe (restriction minimale) : la plupart des appels d'objets runtime
(team.chatroom.read(), team.members.reduce_pattern(), catalogue.*) n'ont PAS
besoin de permission — ce sont des opérations internes déjà autorisées par le
contexte FSM. Seuls nécessitent une vérification :
  - l'accès DAEMON (daemon.*) : le daemon expose le système → borné par la
    table privileges (kind='path'), op='exec'. Les actions sensibles
    (agents, spawn, kill, config…) exigent agent_with_root.
  - les lectures/écritures de fichiers et les exécutions de commandes → déjà
    gérées par ShellAuth / privileges (kind='cmd' / kind='path'), PAS ici.

Convention de mapping daemon.<methode> → /runtime/daemon/<methode>.
MAX_UINT32 = TOUJOURS. Best-effort : si le catalogue n'est pas joignable, on
refuse l'accès daemon (fail-safe) ; un agent sans règle n'accède PAS au daemon.
"""

from __future__ import annotations

import re
from typing import Optional

# Actions daemon nécessitant un privilège agent_with_root (accès système).
ROOT_ACTIONS = {
    "daemon.agents", "daemon.spawn", "daemon.kill", "daemon.stop",
    "daemon.restart", "daemon.deploy", "daemon.config", "daemon.logs",
    "daemon.install", "daemon.shell",
}

_CALL_RE = re.compile(r"^([\w.$]+?)\(([^)]*)\)")


def runtime_path(path: str) -> str:
    """Mappe une chaîne daemon.<m> sur /runtime/daemon/<m>."""
    m = _CALL_RE.search(path)
    callable_part = m.group(1) if m else path
    segs = callable_part.split(".")
    if segs and segs[0] != "daemon":
        return f"/runtime/daemon/{segs[0]}"
    return f"/runtime/daemon/{'/'.join(segs[1:]) or 'root'}"


def requires_root(path: str) -> bool:
    for a in ROOT_ACTIONS:
        if path.startswith(a + ".") or path == a or a in path:
            return True
    return False


def is_daemon_call(path: str) -> bool:
    return path.startswith("daemon.") or path == "daemon"


def check_access(agent_id, path: str, op: str = "exec") -> bool:
    """Vrai si l'agent a le droit d'accéder au daemon via cette chaîne.

    Refuse par défaut (fail-safe) : seul un privilège explicite sur
    /runtime/daemon/* accorde l'accès. Les actions ROOT_ACTIONS exigent
    agent_with_root."""
    if not is_daemon_call(path):
        return True   # pas un accès daemon → pas de permission ici
    rpath = runtime_path(path)
    try:
        from services.api.handlers.catalogue_local import _get
        db = _get()
        level = "agent_with_root" if requires_root(path) else "agent"
        return db.check_privilege(rpath, level=level, op=op, kind="path",
                                  agent_id=int(agent_id))
    except Exception:
        return False   # fail-safe : pas de catalogue → pas de daemon


def access_summary(agent_id, path: str) -> dict:
    return {
        "daemon": is_daemon_call(path),
        "runtime_path": runtime_path(path) if is_daemon_call(path) else None,
        "required_level": "agent_with_root" if requires_root(path) else "agent",
        "allowed": check_access(agent_id, path),
    }
