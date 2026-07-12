"""Contrat PUBLIC du service `installer_worker` : Worker installeur : consomme install_jobs et exécute les recettes."""

KIND = "service"
NAME = "installer_worker"
ENTRYPOINT = "service.py"
RUNS = "run_installer_service"

# Dépendances requises pour fonctionner (sinon bloqué par le superviseur).
DEPENDS = [
    {"pip": "litellm", "min": "1.0"},
    {"bin": "docker"},
]
