"""Contrat PUBLIC du service `watch_installed` : Watcher : scanne périodiquement les outils installés (cache état)."""

KIND = "service"
NAME = "watch_installed"
ENTRYPOINT = "service.py"
RUNS = "watch_installed_tools"
