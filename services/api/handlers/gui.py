"""Routes GUI — traducteur de fenêtre + simulation d'actions.

Permet au backend (mgx/agents/tests) de comprendre l'état de la GUI et de
simuler des actions SANS screenshot :

  gui/inspect  → file d'attente d'une commande d'inspection DOM
  gui/act      → file d'attente d'une action (click/drag/type)
  gui/poll     → le frontend poll : récupère les commandes en attente
  gui/result   → le frontend poste le résultat d'une commande exécutée
  gui/status   → le backend lit le résultat d'une commande (par id ou la plus récente)

Flux : backend POST gui/inspect → command_id. Le frontend (poller React)
poll gui/poll, exécute l'action dans sa webview, POST gui/result. Le backend
POST gui/status {command_id} → résultat.
"""

import json
import threading
import time
import uuid
from typing import Any, Dict, Optional

from services.api.router import register

_LOCK = threading.Lock()
# command_id → {"type", "params", "status": pending|done|error, "result", "created", "window"}
_QUEUE: Dict[str, dict] = {}
# Ordre FIFO : liste des ids en attente (pour le poll).
_PENDING: list[str] = []
_MAX_COMMANDS = 200


def _new_command(cmd_type: str, params: dict, window: str = "") -> dict:
    cid = f"gui_{uuid.uuid4().hex[:8]}"
    with _LOCK:
        _QUEUE[cid] = {
            "id": cid, "type": cmd_type, "params": params,
            "status": "pending", "result": None, "created": time.time(),
            "window": window,
        }
        _PENDING.append(cid)
        # Purgé des vieux terminés
        if len(_QUEUE) > _MAX_COMMANDS:
            for old in list(_QUEUE.keys()):
                if _QUEUE[old]["status"] != "pending" and _QUEUE[old]["created"] < time.time() - 600:
                    _QUEUE.pop(old, None)
    return _QUEUE[cid]


def op_gui_inspect(params: dict) -> Dict[str, Any]:
    """Demande une inspection DOM de la GUI (fenêtre courante du frontend)."""
    cmd = _new_command("inspect", {"what": params.get("what", "dom"), **{k: v for k, v in params.items() if k != "what"}},
                       window=params.get("window", ""))
    return {"status": "ok", "command_id": cmd["id"], "type": "inspect"}


def op_gui_act(params: dict) -> Dict[str, Any]:
    """Demande la simulation d'une action dans la GUI.

    action : click | drag | type | hover
    params : action + coordonnées / cible.
    Ex. click: {action:"click", x:100, y:50}
        drag:  {action:"drag", from:{x,y}, to:{x,y}}
        type:  {action:"type", x:100, y:50, text:"bonjour"}
    """
    action = params.get("action", "")
    if not action:
        return {"status": "error", "error": "action requis"}
    cmd = _new_command("act", {"action": action, **params}, window=params.get("window", ""))
    return {"status": "ok", "command_id": cmd["id"], "type": "act", "action": action}


def op_gui_poll(params: dict) -> Dict[str, Any]:
    """Poll du frontend : renvoie les commandes en attente (FIFO).

    Si params.window est fourni, ne renvoie que les commandes visant cette
    fenêtre (ou sans cible). Sinon, renvoie les commandes en attente.
    """
    import time as _t
    _LAST_POLL[0] = _t.time()
    target = params.get("window", "")
    with _LOCK:
        if not _PENDING:
            return {"status": "ok", "commands": [], "count": 0, "last_poll": _LAST_POLL[0]}
        cids = list(_PENDING)
        taken = []
        keep = []
        for cid in cids:
            c = _QUEUE.get(cid)
            if not c:
                continue
            cw = (c.get("params") or {}).get("window", "")
            if target and cw and cw != target:
                keep.append(cid)  # pas pour cette fenêtre → on laisse
                continue
            taken.append(cid)
        _PENDING[:] = keep
        cmds = []
        for cid in taken:
            c = _QUEUE.get(cid)
            if c:
                cmds.append({"id": c["id"], "type": c["type"], "params": c["params"]})
    return {"status": "ok", "commands": cmds, "count": len(cmds), "last_poll": _LAST_POLL[0]}


_LAST_POLL = [0.0]


def op_gui_result(params: dict) -> Dict[str, Any]:
    """Le frontend poste le résultat d'une commande exécutée."""
    cid = params.get("command_id", "")
    if not cid:
        return {"status": "error", "error": "command_id requis"}
    ok = params.get("ok", True)
    with _LOCK:
        c = _QUEUE.get(cid)
        if not c:
            return {"status": "error", "error": f"commande '{cid}' introuvable"}
        c["status"] = "done" if ok else "error"
        c["result"] = params.get("result")
        c["done_at"] = time.time()
    return {"status": "ok", "command_id": cid}


def op_gui_status(params: dict) -> Dict[str, Any]:
    """Le backend lit le résultat d'une commande (par id ou la plus récente)."""
    cid = params.get("command_id", "")
    with _LOCK:
        if cid:
            c = _QUEUE.get(cid)
            if not c:
                return {"status": "error", "error": f"commande '{cid}' introuvable"}
        else:
            # Plus récente commande terminée
            done = [c for c in _QUEUE.values() if c["status"] in ("done", "error")]
            if not done:
                return {"status": "ok", "pending": True, "result": None}
            c = max(done, key=lambda x: x.get("done_at", 0))
    return {"status": "ok", "command_id": c["id"], "type": c["type"],
            "command_status": c["status"], "result": c["result"]}


register("gui/inspect", op_gui_inspect)
register("gui/act",     op_gui_act)
register("gui/poll",    op_gui_poll)
register("gui/result",  op_gui_result)
register("gui/status",  op_gui_status)
