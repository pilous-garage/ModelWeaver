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


def op_team_add_member(params):
    """Ajoute un membre à une équipe à chaud."""
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    team_name = f"team:{name}" if not name.startswith("team:") else name
    agent_name = params.get("agent_name", "")
    role = params.get("role", "")
    if not agent_name or not role:
        return {"status": "error", "error": "agent_name et role requis"}
    return _mgr.add_member(
        team_name,
        agent_name=agent_name,
        role=role,
        occupation=params.get("occupation", "noncontinue"),
        provider_ref=params.get("provider_ref", ""),
        model_ref=params.get("model_ref", ""),
    )


def op_team_set_leader(params):
    """Définit ou remplace le team_leader d'une équipe."""
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    team_name = f"team:{name}" if not name.startswith("team:") else name
    agent_name = params.get("agent_name", "")
    role = params.get("role", "")
    if not agent_name or not role:
        return {"status": "error", "error": "agent_name et role requis"}
    return _mgr.set_leader(
        team_name,
        agent_name=agent_name,
        role=role,
        occupation=params.get("occupation", "continue"),
        provider_ref=params.get("provider_ref", ""),
        model_ref=params.get("model_ref", ""),
    )


def op_team_init_workspace(params):
    """Crée un workspace pour une équipe et lie le director.

    Crée le workspace DB, enregistre le director, et retourne
    le workspace_id.
    """
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    team_name = f"team:{name}" if not name.startswith("team:") else name
    team = _mgr.get(team_name)
    if not team:
        return {"status": "error", "error": f"team inconnue: {name}"}

    ws_name = params.get("workspace_name", name)
    ws_desc = params.get("workspace_description", f"Espace de travail de l'équipe {name}")

    try:
        from modules.sql.workspace import WorkspaceDB, TaskRepository
        from services.team_manager import _set_workspace_director
        from services._common import mw_home
        import uuid

        ws_id = f"ws_{uuid.uuid4().hex[:8]}"
        home = mw_home()
        ws_db_path = home / "workspaces" / ws_id / "workspace.db"
        ws_db_path.parent.mkdir(parents=True, exist_ok=True)
        wdb = WorkspaceDB(str(ws_db_path))
        wdb.workspaces.create(ws_id, name=ws_name, description=ws_desc)
        tr = TaskRepository(wdb.conn, ws_id)

        # Lier le workspace à la team
        team._set_workspace_director(ws_id, team_name)

        return {
            "status": "ok",
            "workspace_id": ws_id,
            "workspace_name": ws_name,
            "db_path": str(ws_db_path),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_team_create(params):
    """Crée une équipe depuis un manifest YAML ou des paramètres.

    Si ``yaml`` est fourni, crée le fichier .team.yaml et
    enregistre l'équipe. Sinon, crée une équipe simple avec
    les paramètres fournis.
    """
    yaml_content = params.get("yaml", "")
    name = params.get("name", "")

    if yaml_content:
        # Créer depuis un YAML
        if not name:
            import yaml
            data = yaml.safe_load(yaml_content)
            name = (data or {}).get("name", "")
        if not name:
            return {"status": "error", "error": "name introuvable dans le YAML"}

        from services._common import mw_home
        manifests_dir = mw_home() / "manifests" / "teams"
        manifests_dir.mkdir(parents=True, exist_ok=True)
        path = manifests_dir / f"{name}.team.yaml"
        path.write_text(yaml_content)

        # Enregistrer
        from services.team_spec import TeamSpec
        spec = TeamSpec.from_yaml(str(path))
        _mgr.register(spec)

        return {"status": "ok", "name": name, "path": str(path)}

    if not name:
        return {"status": "error", "error": "name ou yaml requis"}

    return {"status": "error", "error": "Création par paramètres non implémentée"}


# ── Route registration ─────────────────────────────────────────────────

register("team/list",              op_team_list)
register("team/get",               op_team_get)
register("team/start",             op_team_start)
register("team/stop",              op_team_stop)
register("team/restart",           op_team_restart)
register("team/delegate",          op_team_delegate)
register("team/chat",              op_team_chat)
register("team/add-member",        op_team_add_member)
register("team/set-leader",        op_team_set_leader)
register("team/init-workspace",    op_team_init_workspace)
register("team/create",            op_team_create)
