"""Routes daemon pour la gestion des ressources Docker (docker/*).

Expose le DockerManager (services/docker_manager) au daemon : caches,
fork+install, association agent↔conteneur, batteries de tests persistantes.
"""

from services.api.router import register
from services.docker_manager.service import get_docker_manager


def _mgr():
    return get_docker_manager()


# ── Caches ─────────────────────────────────────────────────────

def op_docker_caches_list(params):
    """Liste les caches Docker enregistrés."""
    return {"caches": _mgr().list_caches()}


def op_docker_cache_register(params):
    """Enregistre une image existante comme cache."""
    name = params.get("name", "")
    image = params.get("image", "")
    if not name or not image:
        return {"status": "error", "error": "name et image requis"}
    return _mgr().register_cache(name, image)


def op_docker_cache_create(params):
    """Crée un cache : fork image + install d'outils (installeur MW) + commit."""
    name = params.get("name", "")
    base = params.get("base", "python:3.12-slim")
    tools = params.get("tools", []) or []
    if not name:
        return {"status": "error", "error": "name requis"}
    return _mgr().create_cache(name, base_image=base, tools=tools)


# ── Fork / association ─────────────────────────────────────────

def op_docker_fork(params):
    """Fork un cache en conteneur persistant, associé à un testeur."""
    cache = params.get("cache", "")
    agent_id = params.get("agent_id", "")
    project_id = params.get("project_id", "")
    container = params.get("container", "")
    if not cache:
        return {"status": "error", "error": "cache requis"}
    return _mgr().fork(cache_name=cache, container_name=container or None,
                       agent_id=agent_id, project_id=project_id)


def op_docker_associate(params):
    """Associe un agent testeur à un conteneur."""
    agent_id = params.get("agent_id", "")
    container = params.get("container", "")
    project_id = params.get("project_id", "")
    if not agent_id or not container:
        return {"status": "error", "error": "agent_id et container requis"}
    return _mgr().associate(agent_id, container, project_id)


def op_docker_get_for_agent(params):
    """Retourne le conteneur associé à un testeur (ou null)."""
    agent_id = params.get("agent_id", "")
    project_id = params.get("project_id", "")
    if not agent_id:
        return {"status": "error", "error": "agent_id requis"}
    c = _mgr().get_for_agent(agent_id, project_id)
    return {"container": c}


# ── Batteries de tests persistantes ────────────────────────────

def op_docker_run_tests(params):
    """Exécute une batterie de tests dans un conteneur persistant (sans le
    supprimer). command peut être une string (ex: "pytest -q") ou une liste."""
    container = params.get("container", "")
    command = params.get("command", "")
    if not container or not command:
        return {"status": "error", "error": "container et command requis"}
    if isinstance(command, str):
        import shlex
        command = shlex.split(command)
    workdir = params.get("workdir", "/workspace")
    timeout = int(params.get("timeout", 600))
    return _mgr().run_tests(container, command, workdir=workdir, timeout=timeout)


def op_docker_snapshot(params):
    """Commit l'état du conteneur en image (sauvegarde)."""
    container = params.get("container", "")
    image = params.get("image", "")
    if not container:
        return {"status": "error", "error": "container requis"}
    return _mgr().snapshot(container, image or None)


def op_docker_release(params):
    """Arrête et supprime un conteneur (libère la ressource)."""
    container = params.get("container", "")
    if not container:
        return {"status": "error", "error": "container requis"}
    return _mgr().release(container)


def op_docker_status(params):
    """État de tous les conteneurs gérés."""
    return _mgr().status_all()


# ── Enregistrement ─────────────────────────────────────────────

register("docker/caches/list",         op_docker_caches_list)
register("docker/cache/register",      op_docker_cache_register)
register("docker/cache/create",        op_docker_cache_create)
register("docker/fork",                op_docker_fork)
register("docker/associate",           op_docker_associate)
register("docker/get-for-agent",       op_docker_get_for_agent)
register("docker/run-tests",           op_docker_run_tests)
register("docker/snapshot",            op_docker_snapshot)
register("docker/release",             op_docker_release)
register("docker/status",              op_docker_status)
