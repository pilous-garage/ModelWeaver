"""Routes daemon pour les équipes (team/*).

Les routes dynamiques (team/{team_name}/*) sont enregistrées par
TeamManager au chargement de chaque .team.yaml. Ce fichier définit
les routes statiques pour la gestion globale des équipes.
"""

from services.api.router import register
from services.team_manager import TeamManager

_mgr = TeamManager()


def op_team_list(params):
    """Liste toutes les équipes enregistrées."""
    teams = _mgr.list()
    return {"teams": teams, "count": len(teams)}


def op_team_get(params):
    """Détail d'une équipe par nom."""
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    team_name = f"team:{name}" if not name.startswith("team:") else name
    team = _mgr.get(team_name)
    if not team:
        return {"status": "error", "error": f"team inconnue: {name}"}
    return team.status_info()


def op_team_start(params):
    """Démarre une équipe ou un agent spécifique."""
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    team_name = f"team:{name}" if not name.startswith("team:") else name
    _mgr.start(team_name, agent_name=params.get("agent_name"))
    return {"status": "ok", "name": name}


def op_team_stop(params):
    """Arrête une équipe ou un agent spécifique."""
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    team_name = f"team:{name}" if not name.startswith("team:") else name
    _mgr.stop(team_name, agent_name=params.get("agent_name"))
    return {"status": "ok", "name": name}


def op_team_restart(params):
    """Redémarre une équipe."""
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    team_name = f"team:{name}" if not name.startswith("team:") else name
    _mgr.restart(team_name)
    return {"status": "ok", "name": name}


def op_team_delegate(params):
    """Délègue une requête à un agent d'une équipe."""
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    team_name = f"team:{name}" if not name.startswith("team:") else name
    return _mgr.delegate(
        team_name,
        request=params.get("request", ""),
        entrypoint=params.get("entrypoint", "main"),
        target=params.get("target"),
        provider_ref=params.get("provider_ref", ""),
        model_ref=params.get("model_ref", ""),
    )


def op_team_chat(params):
    """Envoie un message de chat à un agent d'une équipe."""
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    team_name = f"team:{name}" if not name.startswith("team:") else name
    return _mgr.chat(
        team_name,
        message=params.get("message", params.get("request", "")),
        target=params.get("target"),
        provider_ref=params.get("provider_ref", ""),
        model_ref=params.get("model_ref", ""),
    )


# ── Route registration ─────────────────────────────────────────────────

register("team/list",     op_team_list)
register("team/get",      op_team_get)
register("team/start",    op_team_start)
register("team/stop",     op_team_stop)
register("team/restart",  op_team_restart)
register("team/delegate", op_team_delegate)
register("team/chat",     op_team_chat)
