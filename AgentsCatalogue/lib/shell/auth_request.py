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
        }

    def __repr__(self) -> str:
        return (
            f"AuthorizationRequest("
            f"id={self.request_id}, agent={self.agent_id}, "
            f"action={self.action}, type={self.request_type.value}, "
            f"status={self.status.value})"
        )


AuthHandler = Callable[[AuthorizationRequest], None]


class AuthRequestHandler:
    """Gestionnaire de demandes d'autorisation.

    Singleton au niveau module — partagé par tous les shells."""

    def __init__(self):
        self._requests: Dict[str, AuthorizationRequest] = {}
        self._pending_user_list: List[str] = []  # request_ids en attente humaine
        self._handlers: List[AuthHandler] = []

    # ── soumission ────────────────────────────────────

    def submit(self, request: AuthorizationRequest) -> AuthorizationRequest:
        self._requests[request.request_id] = request

        if request.request_type == RequestType.PENDING_USER:
            self._pending_user_list.append(request.request_id)

        for handler in self._handlers:
            handler(request)

        return request

    def register_handler(self, handler: AuthHandler) -> None:
        self._handlers.append(handler)

    def unregister_handler(self, handler: AuthHandler) -> None:
        self._handlers.remove(handler)

    # ── résolution ────────────────────────────────────

    def approve(self, request_id: str, approver_id: str) -> bool:
        req = self._requests.get(request_id)
        if req is None:
            return False
        req.approve(approver_id)
        self._pending_user_list = [r for r in self._pending_user_list if r != request_id]
        return True

    def deny(self, request_id: str, approver_id: str, reason: str = "") -> bool:
        req = self._requests.get(request_id)
        if req is None:
            return False
        req.deny(approver_id, reason)
        self._pending_user_list = [r for r in self._pending_user_list if r != request_id]
        return True

    def cancel(self, request_id: str) -> bool:
        req = self._requests.get(request_id)
        if req is None:
            return False
        req.cancel()
        self._pending_user_list = [r for r in self._pending_user_list if r != request_id]
        return True

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

    def count_pending_user(self) -> int:
        return len(self._pending_user_list)

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
        self._handlers.clear()

    def __repr__(self) -> str:
        pending = sum(1 for r in self._requests.values() if r.status == RequestStatus.PENDING)
        return (
            f"AuthRequestHandler(pending={pending}, "
            f"pending_user={len(self._pending_user_list)}, "
            f"total={len(self._requests)})"
        )


# Module-level singleton (partagé par tous les Shell)
request_handler = AuthRequestHandler()