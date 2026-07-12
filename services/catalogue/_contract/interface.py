"""Contrat PUBLIC du service `catalogue` : serveur HTTP de synchro du catalogue."""

KIND = "service"
NAME = "catalogue"
ENTRYPOINT = "service.py"
RUNS = "main"

DEPENDS = [
    {"pip": "psutil"},
    {"pip": "requests"},
]
