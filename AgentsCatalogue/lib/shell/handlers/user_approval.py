"""User approval handler — file d'attente pour validation humaine.

Les demandes PENDING_USER sont accumulées dans
pending_user_authorizations. L'utilisateur ou l'admin peut
les consulter et les approuver/refuser plus tard."""

from typing import Any


def user_approval_handler(request: Any) -> None:
    """Handler : enregistre la demande dans la file d'attente.

    Stub pour notification externe (email, dashboard, etc.)."""
    print(
        f"[USER_APPROVAL] Demande en attente : {request.request_id} "
        f"({request.action} par {request.agent_id})",
    )