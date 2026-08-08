"""Routes Autorisations — auth/list (demandes en attente) + auth/decide.

Le GUI (panneau autorisations) liste les demandes d'autorisation en attente
(leader et/ou humain) et peut décider (allow/deny/escalate/ask_reason).

Lire depuis la table persistée auth_requests (AgentsDB) — le request_handler
(in-memory) y écrit à chaque submit/decide.
"""

import json as _json
from typing import Any, Dict

from services.api.router import register


def _db():
    from modules.sql.agents_repo import AgentsDB
    return AgentsDB()


def op_auth_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """Liste les demandes d'autorisation.

    params :
      - level : 'leader' (défaut) | 'human' | 'all'
      - status : 'pending' (défaut) | 'all'
      - team_id : filtre optionnel
    """
    level = params.get("level", "leader")
    status = params.get("status", "pending")
    team_id = params.get("team_id", "")
    db = _db()
    try:
        where = []
        args = []
        if status != "all":
            where.append("status = ?")
            args.append(status)
        if level == "leader":
            where.append("approver_level = 'leader'")
        elif level == "human":
            where.append("approver_level = 'human'")
        if team_id:
            where.append("team_id = ?")
            args.append(team_id)
        sql = "SELECT * FROM auth_requests"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC"
        rows = db.conn.execute(sql, args).fetchall()
        requests = []
        for r in rows:
            d = dict(r)
            try:
                d["target"] = _json.loads(d.get("target") or "{}")
            except Exception:
                d["target"] = {}
            requests.append(d)
        return {"ok": True, "count": len(requests), "requests": requests}
    finally:
        db.close()


def op_auth_decide(params: Dict[str, Any]) -> Dict[str, Any]:
    """Décide sur une demande d'autorisation (côté GUI/humain ou leader).

    params : request_id, decision (allow|deny|escalate|ask_reason),
             scope (once|run|day|forever), reason, approver_id.

    Le request_handler est un singleton PAR PROCESS : une demande créée par un
    agent (process agent-manager) n'est pas dans le handler du daemon. On lit
    donc la demande depuis la BDD, on la reconstruit dans le handler si absente,
    puis on décide et on re-persiste.
    """
    request_id = params.get("request_id", "")
    decision = params.get("decision", "")
    if not request_id or not decision:
        return {"ok": False, "error": "request_id et decision requis"}
    from AgentsCatalogue.lib.shell.auth_request import (
        AuthScope, AuthorizationRequest, RequestType, request_handler)
    import json as _json
    try:
        scope = AuthScope(params.get("scope", "once"))
    except ValueError:
        scope = AuthScope.ONCE
    approver = params.get("approver_id", "human-gui")

    # Si la demande n'est pas dans le handler (autre process), la reconstruire
    # depuis la BDD.
    if request_handler.get(request_id) is None:
        db = _db()
        try:
            row = db.conn.execute(
                "SELECT * FROM auth_requests WHERE request_id = ?",
                (request_id,)).fetchone()
        finally:
            db.close()
        if not row:
            return {"ok": False, "error": "demande introuvable en BDD"}
        try:
            target = _json.loads(row["target"] or "{}")
        except Exception:
            target = {}
        try:
            req_type = RequestType(row["request_type"])
        except ValueError:
            req_type = RequestType.PENDING_LEADER
        try:
            req_scope = AuthScope(row["scope"] or "once")
        except ValueError:
            req_scope = AuthScope.ONCE
        rebuilt = AuthorizationRequest(
            agent_id=row["agent_id"], team_id=row["team_id"],
            action=row["action"], target=target, reason=row["reason"] or "",
            request_type=req_type, scope=req_scope,
            approver_level=row["approver_level"] or "leader",
        )
        rebuilt.request_id = request_id
        rebuilt.created_at = row["created_at"] or rebuilt.created_at
        request_handler._requests[request_id] = rebuilt
        if rebuilt.request_type == RequestType.PENDING_USER:
            request_handler._pending_user_list.append(request_id)
        else:
            request_handler._pending_leader_list.append(request_id)

    return request_handler.decide(
        request_id, str(approver), decision,
        scope=scope, reason=params.get("reason", ""))


register("auth/list", op_auth_list)
register("auth/decide", op_auth_decide)
