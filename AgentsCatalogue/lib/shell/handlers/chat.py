"""Chat auth request handler.

LIVE : notification temps réel avec commande /auth approve|deny
PENDING_USER : notification que la demande part en file d'attente"""

from typing import Any


def chat_notify_handler(request: Any) -> None:
    """Handler : notifie le chat d'une demande d'autorisation.

    Stub : à connecter avec le vrai système de chat."""
    from ..auth_request import RequestType
    if request.request_type == RequestType.LIVE:
        print(
            f"[CHAT] 🔐 Demande LIVE de {request.agent_id} "
            f"(team={request.team_id}): {request.action} "
            f"- /auth approve {request.request_id} "
            f"/auth deny {request.request_id}",
        )
    else:
        print(
            f"[CHAT] 📋 Demande PENDING_USER de {request.agent_id} "
            f"(team={request.team_id}): {request.action} "
            f"- mise en file d'attente ({request.request_id})",
        )