"""Contrat des DEPENDANCES du service `docker_manager`."""

CONSUMES = {
    "module": [
        "modules.container_manager.container_manager",  # ContainerManager
        "modules.installer.installer",                  # Installeur (fork+install)
        "modules.catalogue.catalogue",                  # entrées catalogue
    ],
    "service": [
        "services._common",                              # mw_home
    ],
}
