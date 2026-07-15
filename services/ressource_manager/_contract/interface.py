"""Contrat PUBLIC du service `ressource_manager` : agrégateur global des ressources."""

KIND = "service"
NAME = "ressource_manager"
ENTRYPOINT = "service.py"
RUNS = "RessourceManager"

DEPENDS = [
    {"pip": "psutil"},
]
