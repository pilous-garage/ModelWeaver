"""Auth request handlers — boîte à outils pour résoudre les demandes.

Usage :
    from AgentsCatalogue.lib.shell.handlers import setup_default_handlers
    setup_default_handlers(leader_id="leader-1")  # enregistre les handlers

Handlers enregistrés :
    - chat_notify_handler  : notifie le chat des demandes LIVE et PENDING_USER
    - team_leader_approve  : auto-approve les actions de sa team si leader configuré
    - user_approval_handler: enregistre les PENDING_USER dans la file d'attente
"""

from typing import Optional


def setup_default_handlers(leader_id: Optional[str] = None) -> None:
    """Configure les handlers d'autorisation par défaut.

    Args:
        leader_id: si fourni, le team leader auto-approuve les actions
                   de sa team pour les demandes LIVE uniquement"""
    from ..auth_request import request_handler

    from .chat import chat_notify_handler
    request_handler.register_handler(chat_notify_handler)

    from .user_approval import user_approval_handler
    request_handler.register_handler(user_approval_handler)

    if leader_id:
        from .team_leader import team_leader_auto_approve
        request_handler.register_handler(team_leader_auto_approve)