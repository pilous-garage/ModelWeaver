"""AuthorizationRequest — système de demandes d'autorisation asynchrones.

Deux niveaux de demande :
- LIVE : demande directe (chat), l'agent peut attendre la réponse
- PENDING_USER : file d'attente pour validation humaine,
  l'agent doit continuer sans (continue_anyway=True)

Flow LIVE :
  agent: pskill 1234 → submit LIVE → handlers notify chat
  user: /auth approve xxx → request_handler.approve()
  agent: poll → request.approved → retry

Flow PENDING_USER :
  agent: installer npm → submit PENDING_USER
  handler: add to list → user voit "pending_user_authorizations"
  agent: continue_anyway=True → cherche une alternative
  admin: /auth approve xxx (plus tard)
"""

import time
import uuid
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class RequestStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class RequestType(Enum):
    LIVE = "live"              # demande directe (chat, auto-approve)
    PENDING_USER = "pending_user"  # validation humaine différée
    PENDING_LEADER = "pending_leader"  # validation par le leader de la team


class AuthScope(Enum):
    """Portée d'une autorisation / d'un refus mémorisé.

    - ONCE    : vaut pour une seule exécution (puis consommée).
    - RUN     : vaut pour la durée du run courant de l'agent.
    - DAY     : vaut jusqu'à la fin de la journée.
    - FOREVER : vaut toujours (config durable).
    """
    ONCE = "once"
    RUN = "run"
    DAY = "day"
    FOREVER = "forever"


class AuthGrant:
    """Autorisation ou refus MÉMORISÉ pour un agent/action/cible.

    Distingue les grants (accordés) et les denies (refus mémorisés) — les
    deux avec une portée (scope) pour ne pas re-demander sans fin.

    `source` : qui a accordé ("leader", "human", "auto").
    `agent_id` : à QUI s'applique le grant (peut être un leader héritier).
    """

    def __init__(
        self,
        agent_id: str,
        action: str,
        target_key: str,
        granted: bool,
        scope: AuthScope = AuthScope.ONCE,
        source: str = "leader",
        granted_by: Optional[str] = None,
        created_at: Optional[float] = None,
    ):
        self.agent_id = agent_id
        self.action = action
        self.target_key = target_key
        self.granted = granted  # True = autorisé, False = refus mémorisé
        self.scope = scope
        self.source = source
        self.granted_by = granted_by
        self.created_at = created_at or time.time()
        self._consumed_once = False

    @property
    def is_expired(self) -> bool:
        if self.scope == AuthScope.FOREVER:
            return False
        if self.scope == AuthScope.ONCE:
            return self._consumed_once
        if self.scope == AuthScope.RUN:
            # expire au prochain run — géré par l'agent (run_id)
            return False  # le run garde les grants en mémoire
        if self.scope == AuthScope.DAY:
            import datetime
            now = datetime.datetime.now()
            eod = now.replace(hour=23, minute=59, second=59, microsecond=0)
            return time.time() > eod.timestamp()
        return False

    def consume_once(self) -> None:
        self._consumed_once = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "action": self.action,
            "target_key": self.target_key,
            "granted": self.granted,
            "scope": self.scope.value,
            "source": self.source,
            "granted_by": self.granted_by,
            "created_at": self.created_at,
            "consumed_once": self._consumed_once,
        }


class AuthorizationRequest:
    """Demande d'autorisation pour une action sensible."""

    def __init__(
        self,
        agent_id: str,
        team_id: Optional[str],
        action: str,
        target: Dict[str, Any],
        reason: str = "",
        request_type: RequestType = RequestType.LIVE,
        ttl: float = 300.0,
        scope: AuthScope = AuthScope.ONCE,
        approver_level: str = "leader",  # "leader" d'abord, puis "human"
    ):
        self.request_id = uuid.uuid4().hex[:12]
        self.agent_id = agent_id
        self.team_id = team_id
        self.action = action
        self.target = target
        self.reason = reason
        self.request_type = request_type
        self.status = RequestStatus.PENDING
        self.created_at = time.time()
        self.resolved_at: Optional[float] = None
        self.approver_id: Optional[str] = None
        self.rejection_reason: Optional[str] = None
        self.ttl = ttl
        self.scope = scope
        self.approver_level = approver_level  # leader → human (escalade)
        # Portée appliquée à la décision (fixée au moment de la résolution).
        self.resolved_scope: Optional[AuthScope] = None

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl

    @property
    def is_resolved(self) -> bool:
        return self.status in (RequestStatus.APPROVED, RequestStatus.DENIED)

    @property
    def continue_anyway(self) -> bool:
        """Les demandes PENDING_USER ne bloquent pas l'agent — il doit continuer sans."""
        return self.request_type == RequestType.PENDING_USER

    def approve(self, approver_id: str) -> None:
        self.status = RequestStatus.APPROVED
        self.approver_id = approver_id
        self.resolved_at = time.time()

    def deny(self, approver_id: str, reason: str = "") -> None:
        self.status = RequestStatus.DENIED
        self.approver_id = approver_id
        self.rejection_reason = reason
        self.resolved_at = time.time()

    def cancel(self) -> None:
        self.status = RequestStatus.CANCELLED
        self.resolved_at = time.time()

    def expire(self) -> None:
        if self.status == RequestStatus.PENDING:
            self.status = RequestStatus.EXPIRED
            self.resolved_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "agent_id": self.agent_id,
            "team_id": self.team_id,
            "action": self.action,
            "target": self.target,
            "reason": self.reason,
            "request_type": self.request_type.value,
            "continue_anyway": self.continue_anyway,
            "status": self.status.value,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "approver_id": self.approver_id,
            "rejection_reason": self.rejection_reason,
            "scope": self.scope.value,
            "approver_level": self.approver_level,
            "resolved_scope": self.resolved_scope.value if self.resolved_scope else None,
        }

    def __repr__(self) -> str:
        return (
            f"AuthorizationRequest("
            f"id={self.request_id}, agent={self.agent_id}, "
            f"action={self.action}, type={self.request_type.value}, "
            f"status={self.status.value})"
        )


AuthHandler = Callable[[AuthorizationRequest], None]


def _target_key(action: str, target: Dict[str, Any]) -> str:
    """Clé canonique d'une cible (path/command) pour comparer les grants."""
    if action == "command":
        return str(target.get("command", "")).strip()
    if action in ("path_read", "path_write"):
        mode = "read" if action == "path_read" else "write"
        return f"{mode}:{str(target.get('path', '')).rstrip('/')}"
    return f"{action}:{sorted(str(v) for v in target.values())}"


class AuthRequestHandler:
    """Gestionnaire de demandes d'autorisation.

    Singleton au niveau module — partagé par tous les shells."""

    def __init__(self):
        self._requests: Dict[str, AuthorizationRequest] = {}
        self._pending_user_list: List[str] = []  # request_ids en attente humaine
        self._pending_leader_list: List[str] = []  # request_ids en attente leader
        self._handlers: List[AuthHandler] = []
        # Grants accordés + denies mémorisés : {agent_id: [AuthGrant]}.
        self._grants: Dict[str, List[AuthGrant]] = {}
        # run_id courant par agent (pour la portée RUN).
        self._run_ids: Dict[str, str] = {}

    # ── soumission ────────────────────────────────────

    def _persist(self, req: 'AuthorizationRequest') -> None:
        """Persiste la demande dans la BDD agents (table auth_requests).

        Le request_handler est en mémoire ; cette table permet au GUI (panneau
        autorisations) de lister/gérer les demandes. Best-effort : un échec
        de persistance ne casse pas le flux.
        """
        try:
            from modules.sql.agents_repo import AgentsDB
            import json as _json
            db = AgentsDB()
            try:
                db.conn.execute("""
                    INSERT OR REPLACE INTO auth_requests
                        (request_id, agent_id, team_id, action, target, reason,
                         scope, approver_level, request_type, status,
                         approver_id, rejection_reason, resolved_scope,
                         created_at, resolved_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    req.request_id, req.agent_id, req.team_id, req.action,
                    _json.dumps(req.target, ensure_ascii=False), req.reason,
                    req.scope.value, req.approver_level, req.request_type.value,
                    req.status.value, req.approver_id, req.rejection_reason,
                    req.resolved_scope.value if req.resolved_scope else None,
                    req.created_at, req.resolved_at,
                ))
            finally:
                db.close()
        except Exception:
            pass

    def submit(self, request: AuthorizationRequest) -> AuthorizationRequest:
        self._requests[request.request_id] = request

        if request.request_type == RequestType.PENDING_USER:
            self._pending_user_list.append(request.request_id)
        elif request.request_type == RequestType.PENDING_LEADER:
            self._pending_leader_list.append(request.request_id)

        self._persist(request)
        for handler in self._handlers:
            handler(request)

        return request

    def register_handler(self, handler: AuthHandler) -> None:
        self._handlers.append(handler)

    def unregister_handler(self, handler: AuthHandler) -> None:
        self._handlers.remove(handler)

    # ── résolution ────────────────────────────────────

    def _remove_from_lists(self, request_id: str) -> None:
        self._pending_user_list = [r for r in self._pending_user_list if r != request_id]
        self._pending_leader_list = [r for r in self._pending_leader_list if r != request_id]

    def _add_grant(self, agent_id: str, req: AuthorizationRequest,
                   granted: bool, approver_id: str, source: str) -> None:
        target_key = _target_key(req.action, req.target)
        g = AuthGrant(
            agent_id=agent_id,
            action=req.action,
            target_key=target_key,
            granted=granted,
            scope=req.resolved_scope or req.scope,
            source=source,
            granted_by=approver_id,
        )
        self._grants.setdefault(agent_id, []).append(g)
        # Nettoyer les grants expirés de cet agent (paresseux).
        self._grants[agent_id] = [
            x for x in self._grants[agent_id] if not x.is_expired
        ]

    def approve(self, request_id: str, approver_id: str,
                scope: Optional[AuthScope] = None) -> bool:
        req = self._requests.get(request_id)
        if req is None:
            return False
        req.resolved_scope = scope or req.scope
        req.approve(approver_id)
        self._remove_from_lists(request_id)
        self._persist(req)
        # Grant pour le DEMANDEUR.
        self._add_grant(req.agent_id, req, True, approver_id,
                        source="leader" if req.approver_level == "leader" else "human")
        # Héritage : si un HUMAIN approuve, le LEADER de la team l'hérite
        # aussi (il pourra l'autoriser lui-même la prochaine fois).
        if req.approver_level == "human" and req.team_id:
            self._add_grant(f"leader:{req.team_id}", req, True, approver_id,
                            source="human-inherit")
        return True

    def deny(self, request_id: str, approver_id: str, reason: str = "",
             scope: Optional[AuthScope] = None) -> bool:
        req = self._requests.get(request_id)
        if req is None:
            return False
        req.resolved_scope = scope or req.scope
        req.deny(approver_id, reason)
        self._remove_from_lists(request_id)
        self._persist(req)
        # Refus mémorisé pour le DEMANDEUR.
        self._add_grant(req.agent_id, req, False, approver_id,
                        source="leader" if req.approver_level == "leader" else "human")
        if req.approver_level == "human" and req.team_id:
            self._add_grant(f"leader:{req.team_id}", req, False, approver_id,
                            source="human-inherit")
        return True

    def escalate(self, request_id: str, approver_id: str) -> bool:
        """Le leader transmet à l'humain (niveau supérieur)."""
        req = self._requests.get(request_id)
        if req is None or req.status != RequestStatus.PENDING:
            return False
        req.approver_level = "human"
        req.request_type = RequestType.PENDING_USER
        self._pending_leader_list = [r for r in self._pending_leader_list
                                     if r != request_id]
        self._pending_user_list.append(request_id)
        self._persist(req)
        return True

    def cancel(self, request_id: str) -> bool:
        req = self._requests.get(request_id)
        if req is None:
            return False
        req.cancel()
        self._remove_from_lists(request_id)
        return True

    # ── grants / denies mémorisés ─────────────────────

    def is_granted(self, agent_id: str, action: str, target: Dict[str, Any],
                   consume_once: bool = True) -> bool:
        """Un grant accordé (non expiré) existe pour cet agent/action/cible ?"""
        key = _target_key(action, target)
        for g in self._grants.get(agent_id, []):
            if g.granted and g.action == action and g.target_key == key \
                    and not g.is_expired:
                if consume_once and g.scope == AuthScope.ONCE:
                    g.consume_once()
                return True
        return False

    def is_denied(self, agent_id: str, action: str, target: Dict[str, Any]) -> bool:
        """Un refus mémorisé (non expiré) existe pour cet agent/action/cible ?"""
        key = _target_key(action, target)
        return any(
            not g.granted and g.action == action and g.target_key == key
            and not g.is_expired
            for g in self._grants.get(agent_id, [])
        )

    def decide(self, request_id: str, approver_id: str, decision: str,
               scope: AuthScope = AuthScope.ONCE, reason: str = "") -> Dict[str, Any]:
        """Point d'entrée du leader/humain.

        decision : allow | deny | escalate | ask_reason
        """
        req = self._requests.get(request_id)
        if req is None:
            return {"ok": False, "error": "demande introuvable"}
        if decision == "allow":
            self.approve(request_id, approver_id, scope=scope)
            return {"ok": True, "request_id": request_id, "decision": "allow",
                    "scope": scope.value}
        if decision == "deny":
            self.deny(request_id, approver_id, reason=reason, scope=scope)
            return {"ok": True, "request_id": request_id, "decision": "deny",
                    "scope": scope.value}
        if decision == "escalate":
            self.escalate(request_id, approver_id)
            return {"ok": True, "request_id": request_id, "decision": "escalate"}
        if decision == "ask_reason":
            return {"ok": True, "request_id": request_id, "decision": "ask_reason",
                    "note": "Demander à l'agent pourquoi il a besoin de cette autorisation"}
        return {"ok": False, "error": f"decision inconnue: {decision}"}

    # ── pending_user list (validation humaine) ────────

    def get_pending_user_authorizations(
        self, agent_id: Optional[str] = None
    ) -> List[AuthorizationRequest]:
        """Retourne les demandes en attente de validation humaine."""
        results = []
        for req_id in list(self._pending_user_list):
            req = self._requests.get(req_id)
            if req is None:
                self._pending_user_list.remove(req_id)
                continue
            if req.is_expired:
                req.expire()
                self._pending_user_list.remove(req_id)
                continue
            if agent_id and req.agent_id != agent_id:
                continue
            results.append(req)
        return results

    def get_pending_leader_authorizations(
        self, team_id: Optional[str] = None
    ) -> List[AuthorizationRequest]:
        """Retourne les demandes en attente de validation par un LEADER."""
        results = []
        for req_id in list(self._pending_leader_list):
            req = self._requests.get(req_id)
            if req is None:
                self._pending_leader_list.remove(req_id)
                continue
            if req.is_expired:
                req.expire()
                self._pending_leader_list.remove(req_id)
                continue
            if team_id and req.team_id != team_id:
                continue
            results.append(req)
        return results

    def count_pending_user(self) -> int:
        return len(self._pending_user_list)

    def count_pending_leader(self) -> int:
        return len(self._pending_leader_list)

    # ── query ─────────────────────────────────────────

    def get(self, request_id: str) -> Optional[AuthorizationRequest]:
        return self._requests.get(request_id)

    def get_pending(
        self, agent_id: Optional[str] = None, team_id: Optional[str] = None
    ) -> List[AuthorizationRequest]:
        results = []
        for req in self._requests.values():
            if req.status != RequestStatus.PENDING:
                continue
            if agent_id and req.agent_id != agent_id:
                continue
            if team_id and req.team_id != team_id:
                continue
            if req.is_expired:
                req.expire()
                continue
            results.append(req)
        return results

    def get_by_agent(self, agent_id: str) -> List[AuthorizationRequest]:
        return [
            req for req in self._requests.values()
            if req.agent_id == agent_id
        ]

    def get_by_action(self, action: str) -> List[AuthorizationRequest]:
        return [
            req for req in self._requests.values()
            if req.action == action
        ]

    def get_by_type(self, request_type: RequestType) -> List[AuthorizationRequest]:
        return [
            req for req in self._requests.values()
            if req.request_type == request_type
        ]

    def clear(self) -> None:
        self._requests.clear()
        self._pending_user_list.clear()
        self._pending_leader_list.clear()
        self._grants.clear()
        self._handlers.clear()

    def __repr__(self) -> str:
        pending = sum(1 for r in self._requests.values() if r.status == RequestStatus.PENDING)
        n_grants = sum(len(v) for v in self._grants.values())
        return (
            f"AuthRequestHandler(pending={pending}, "
            f"pending_leader={len(self._pending_leader_list)}, "
            f"pending_user={len(self._pending_user_list)}, "
            f"grants={n_grants}, total={len(self._requests)})"
        )


# Module-level singleton (partagé par tous les Shell)
request_handler = AuthRequestHandler()