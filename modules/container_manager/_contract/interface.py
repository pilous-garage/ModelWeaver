"""Contrat PUBLIC du module `container_manager` : surface exposée."""

KIND = "module"
NAME = "container_manager"
MODULE = "modules.container_manager.container_manager"
EXPORTS = ['ContainerManager']

# Surface :
#   ContainerManager.run_command(command, volume_mounts) → str
#     Conteneur temporaire (--rm), montages host→conteneur.
#   ContainerManager.list_images() → List[str]
#     Images Docker locales ("repo:tag").
#
#   # Conteneurs persistants / nommés (pas de --rm) — utilisé par
#   # services/docker_manager pour les batteries de tests :
#   ContainerManager.create(name, image, volumes, env, workdir) → (ok, msg)
#   ContainerManager.start(name)                       → (ok, msg)
#   ContainerManager.exec(name, command, workdir, timeout) → {ok, exit_code, stdout, stderr}
#   ContainerManager.commit(name, image_tag)           → (ok, msg)   # snapshot cache
#   ContainerManager.stop(name)                        → (ok, msg)
#   ContainerManager.remove(name, force)               → (ok, msg)
#   ContainerManager.exists(name) → bool
#   ContainerManager.status(name) → "running"|"exited"|"absent"
#   ContainerManager.image_exists(image_tag) → bool
