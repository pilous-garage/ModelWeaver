"""Contrat PUBLIC du service `watch_sysstate` : Watcher : met à jour et publie l'état système périodiquement."""

KIND = "service"
NAME = "watch_sysstate"
ENTRYPOINT = "service.py"
RUNS = "watch_system_state"

DEPENDS = [
    {"pip": "psutil"},
]
