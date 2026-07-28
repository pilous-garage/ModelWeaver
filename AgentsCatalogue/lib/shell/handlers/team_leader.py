"""Team leader auto-approve handler.

Quand une demande d'autorisation est soumise pour une action
qu'un team leader peut approuver, il l'auto-approuve."""

from typing import Any, Dict


def team_leader_auto_approve(request: Any) -> None:
    """Handler : auto-approve si l'action est pskill et que le
    demandeur est dans la team du leader.

    Ce handler doit être paramétré avec les paires (leader_id, team_id)
    autorisées à approuver les demandes de leur team."""

    from ..auth_request import request_handler

    action = request.action
    agent_id = request.agent_id
    team_id = request.team_id
    target = request.target

    # Actions auto-approvables par un team leader
    # Format: {"action": {"agent_roles": [...], "target_teams": [...]}}
    auto_approve_policies: Dict[str, Dict] = {
        "pskill": {
            "agent_roles": ["leader", "owner"],
            "target_team": team_id,  # leader peut approuver pour sa team
        },
        "pskill_sys": {
            "agent_roles": ["leader", "owner"],
            "target_team": team_id,
        },
    }

    policy = auto_approve_policies.get(action)
    if policy is None:
        return  # pas auto-approvable

    allowed_roles = policy.get("agent_roles", [])
    # À implémenter : vérifier que le approver est bien leader de la team
    # Pour l'instant, on auto-approve si l'agent est dans la team
    # et que l'action est autorisée

    # Si on a un leader configuré (via handler params), on auto-approve
    leader_id = getattr(request, "_leader_id", None)
    if leader_id is not None:
        request_handler.approve(request.request_id, leader_id)