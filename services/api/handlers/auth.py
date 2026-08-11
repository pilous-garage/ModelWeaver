"""Routes Autorisations — auth/list (demandes en attente) + auth/decide.

Le GUI (panneau autorisations) liste les demandes d'autorisation en attente
(leader et/ou humain) et peut décider (allow/deny/escalate/ask_reason).

Lire depuis la table persistée auth_requests (AgentsDB) — le request_handler
(in-memory) y écrit à chaque submit/decide.
"""

import json as _json
from typing import Any, Dict, Optional

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


def op_auth_ask_human(params: Dict[str, Any]) -> Dict[str, Any]:
    """Demande DIFFÉRÉE à l'humain (action avec ask=human/human_root).

    Utilisée quand use_privilege signale ask_pending : l'action est autorisée
    mais exige une confirmation humaine (éventuellement avec root).

    params :
      - agent_id   : l'agent qui demande.
      - team_id    : team de l'agent (optionnel).
      - action     : 'path_read' | 'path_write' | 'command' | 'root_privilege'…
      - target     : {path|command|chemin_ref, …}.
      - reason     : justification (optionnel).
      - ask        : 'human' | 'human_root' | 'security_supervisor'
                     (signale si l'approbation exige le root de l'humain).
    Retourne {ok, request_id} — la demande est PENDING_USER, l'agent continue
    (différé), l'humain répond via auth/decide.
    """
    from AgentsCatalogue.lib.shell.auth_request import (
        AuthorizationRequest, RequestType, request_handler)
    agent_id = str(params.get("agent_id", ""))
    action = params.get("action", "")
    target = params.get("target", {})
    ask = params.get("ask", "human")
    if not agent_id or not action:
        return {"ok": False, "error": "agent_id et action requis"}
    try:
        req = AuthorizationRequest(
            agent_id=agent_id,
            team_id=str(params.get("team_id", "")) or None,
            action=action,
            target=target if isinstance(target, dict) else {"target": target},
            reason=params.get("reason", ""),
            request_type=RequestType.PENDING_USER,
        )
        # ask=security_supervisor → file du superviseur désigné (sinon refus
        # propre : pas de superviseur configuré).
        if ask == "security_supervisor":
            sup = _supervisor_for(str(params.get("team_id", "")) or "")
            if sup is None:
                return {"ok": False,
                        "error": "security_supervisor : aucun superviseur désigné"}
            req.approver_level = "human"
            req.target["supervisor_agent_id"] = sup
            req = request_handler.submit(req)
            return {"ok": True, "request_id": req.request_id, "ask": ask,
                    "pending": "supervisor", "supervisor_agent_id": sup}
        # Une demande ask_human va DIRECTEMENT à l'humain (pas au leader).
        req.approver_level = "human"
        req = request_handler.submit(req)
        return {"ok": True, "request_id": req.request_id, "ask": ask,
                "pending": "user"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def op_auth_ask_root(params: Dict[str, Any]) -> Dict[str, Any]:
    """Un agent demande à l'humain de lui OCTROYER root (privilège durable).

    params : agent_id, reason, deadline (date ISO, optionnel), team_id.
    Flow : crée une demande PENDING_USER (action='root_privilege'). À
    l'approbation (auth/decide), on crée un grant privileges `privileged`
    sur /runtime/root/{agent_id}, scoped à l'agent.
    """
    from AgentsCatalogue.lib.shell.auth_request import (
        AuthorizationRequest, RequestType, request_handler)
    agent_id = str(params.get("agent_id", ""))
    if not agent_id:
        return {"ok": False, "error": "agent_id requis"}
    try:
        req = AuthorizationRequest(
            agent_id=agent_id,
            team_id=str(params.get("team_id", "")) or None,
            action="root_privilege",
            target={"agent_id": agent_id,
                    "deadline": params.get("deadline", "")},
            reason=params.get("reason", "demande de privilèges root"),
            request_type=RequestType.PENDING_USER,
        )
        req.approver_level = "human"
        req = request_handler.submit(req)
        return {"ok": True, "request_id": req.request_id,
                "action": "root_privilege", "pending": "user"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def op_auth_decide(params: Dict[str, Any]) -> Dict[str, Any]:
    """Décide sur une demande d'autorisation (côté GUI/humain ou leader).

    params : request_id, decision (allow|deny|escalate|ask_reason),
             scope (once|run|day|forever|team|global), reason, approver_id.

    PORTÉE (options de l'humain) : la décision `allow` crée, via le writer
    PRIVÉ, un grant privileges scoped selon la portée choisie :
      - once/run/day/forever → grant scoped à l'agent demandeur.
      - team   → grant scoped à la team du demandeur.
      - global → grant scoped -1 (tous les agents).

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

    result = request_handler.decide(
        request_id, str(approver), decision,
        scope=scope, reason=params.get("reason", ""))

    # Approbation → grant privileges via le writer PRIVÉ, scoped par portée.
    if decision == "allow" and result.get("ok", True):
        req = request_handler.get(request_id)
        if req is not None:
            _grant_from_approval(req, scope, approver)

    return result


def _grant_from_approval(req, scope, approver) -> None:
    """Crée un grant privileges (writer privé) depuis une approbation.

    Portée : once/run/day/forever → agent demandeur ; team → sa team ;
    global → -1 (tous). Le grant porte le mode exec (action exécutée) et le
    chemin runtime/commande de la demande.

    Cas spécial root_privilege : crée un grant `privileged` sur
    /runtime/root/{agent_id}, scoped à l'agent (deadline si demandée)."""
    try:
        from services.api.handlers.catalogue_local import op_priv_create
        target = req.target if isinstance(req.target, dict) else {}
        scope_val = getattr(req.scope, "value", "") if hasattr(req, "scope") else ""
        agent_id = -1
        team = -1
        if scope_val == "team":
            try:
                team = int(req.team_id)
            except (TypeError, ValueError):
                team = -1
        elif scope_val != "global":
            try:
                agent_id = int(req.agent_id)
            except (TypeError, ValueError):
                agent_id = -1

        # ROOT PRIVILEGE : grant privileged durable pour l'agent
        if req.action == "root_privilege":
            tgt_agent = target.get("agent_id") or req.agent_id
            op_priv_create({
                "chemin_ref": f"/runtime/root/{tgt_agent}",
                "kind": "path", "level": 100,
                "exec": "----", "privileged": "---p",
                "agent_id": int(tgt_agent), "team": -1,
                "deadline": target.get("deadline", ""),
                "description": f"root octroyé par {approver} via auth/decide",
                "token": "write_catalogue_priv",
            })
            return

        chemin_ref = target.get("chemin_ref") or target.get("path") or \
            target.get("command") or target.get("command_name") or ""
        if not chemin_ref:
            return
        if req.action == "command":
            kind = "cmd"
        elif req.action in ("path_read", "path_write"):
            kind = "path"
        else:
            kind = "ref"
        op_priv_create({
            "chemin_ref": chemin_ref, "kind": kind, "level": 100,
            "exec": "---x", "privileged": "----",
            "agent_id": agent_id, "team": team,
            "description": f"granté par {approver} via auth/decide",
            "token": "write_catalogue_priv",
        })
    except Exception:
        pass   # best-effort : la décision auth/decide reste persistée


def op_auth_ask_human_anyway(params: Dict[str, Any]) -> Dict[str, Any]:
    """Demande à l'humain quand même, malgré un REFUS.

    Quand use_privilege retourne allowed=False (ou un deny grant mémorisé),
    l'agent peut insister : crée une demande PENDING_USER avec le flag
    `override=true` (le refus est signalé dans la demande). L'humain voit
    « l'agent insiste malgré le refus » et tranche.

    params : agent_id, action, target, reason, team_id.
    """
    from AgentsCatalogue.lib.shell.auth_request import (
        AuthorizationRequest, RequestType, request_handler)
    agent_id = str(params.get("agent_id", ""))
    action = params.get("action", "")
    if not agent_id or not action:
        return {"ok": False, "error": "agent_id et action requis"}
    try:
        target = params.get("target", {})
        req = AuthorizationRequest(
            agent_id=agent_id,
            team_id=str(params.get("team_id", "")) or None,
            action=action,
            target=target if isinstance(target, dict) else {"target": target},
            reason=params.get("reason", "insiste malgré un refus"),
            request_type=RequestType.PENDING_USER,
        )
        req.approver_level = "human"
        # flag override : signalé dans la cible pour le panneau GUI
        req.target["override"] = True
        req = request_handler.submit(req)
        return {"ok": True, "request_id": req.request_id, "override": True,
                "pending": "user"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def op_auth_supervisor_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """Liste les superviseurs désignés (security_supervisor)."""
    from modules.sql.catalogue_local import LocalCatalogue
    from modules.sql.schema import _default_local_catalogue_db
    import sqlite3
    try:
        conn = sqlite3.connect(
            f"file:{_default_local_catalogue_db()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM security_supervisor ORDER BY scope, team_id").fetchall()
        conn.close()
        return {"ok": True, "supervisors": [dict(r) for r in rows]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def op_auth_supervisor_set(params: Dict[str, Any]) -> Dict[str, Any]:
    """Désigne un agent superviseur (writer catalogue — pas le writer privé).

    params : supervisor_agent_id, scope ('global'|'team'), team_id (si team),
             active (1/0).
    """
    try:
        from services.api.handlers.catalogue_local import _get
        db = _get("w")
        db._check_write(params.get("token", ""))
        scope = params.get("scope", "global")
        if scope not in ("team", "global"):
            return {"ok": False, "error": "scope invalide (team|global)"}
        db.conn.execute(
            "INSERT INTO security_supervisor "
            "(supervisor_agent_id, scope, team_id, active) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO NOTHING",
            (int(params.get("supervisor_agent_id", 0)), scope,
             int(params.get("team_id", -1)) if scope == "team" else None,
             1 if params.get("active", True) else 0))
        db.conn.commit()
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _supervisor_for(team_id: str) -> Optional[int]:
    """L'agent superviseur d'une team (ou global), ou None."""
    try:
        from modules.sql.schema import _default_local_catalogue_db
        import sqlite3
        conn = sqlite3.connect(
            f"file:{_default_local_catalogue_db()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        row = None
        try:
            if team_id:
                row = conn.execute(
                    "SELECT supervisor_agent_id FROM security_supervisor "
                    "WHERE active = 1 AND scope = 'team' AND team_id = ? "
                    "LIMIT 1", (int(team_id),)).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT supervisor_agent_id FROM security_supervisor "
                    "WHERE active = 1 AND scope = 'global' LIMIT 1").fetchone()
        finally:
            conn.close()
        return row["supervisor_agent_id"] if row else None
    except Exception:
        return None


register("auth/list", op_auth_list)
register("auth/decide", op_auth_decide)
register("auth/ask_human", op_auth_ask_human)
register("auth/ask_root", op_auth_ask_root)
register("auth/ask_human_anyway", op_auth_ask_human_anyway)
register("auth/supervisor/list", op_auth_supervisor_list)
register("auth/supervisor/set", op_auth_supervisor_set)
