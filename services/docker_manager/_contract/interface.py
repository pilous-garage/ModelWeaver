"""Contrat PUBLIC du service `docker_manager` : surface exposée."""

KIND = "service"
NAME = "docker_manager"
MODULE = "services.docker_manager.service"
EXPORTS = ['DockerManager', 'get_docker_manager']

# Surface :
#   Caches (images pré-installées réutilisables) :
#     list_caches()                 -> [{"name", "image", "local"}]
#     register_cache(name, image)   -> {ok, ...}
#     create_cache(name, base, tools) -> {ok, image, ...}  # fork+install+commit
#
#   Fork / association testeur :
#     fork(cache_name, agent_id, project_id, volumes) -> {ok, container, image}
#     associate(agent_id, container_name, project_id) -> {ok, ...}
#     get_for_agent(agent_id, project_id) -> container_name | None
#
#   Batteries de tests persistantes (le conteneur survit) :
#     run_tests(container, command, workdir, timeout) -> {ok, exit_code, stdout, stderr}
#     snapshot(container, image_tag) -> {ok, image}   # commit état
#     release(container)             -> {ok}          # stop+remove
#     status_all()                   -> {containers, count}
