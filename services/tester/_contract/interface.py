"""Contrat PUBLIC du service `tester` : Testeur : lit un script d'actions install/uninstall et enfile les jobs."""

KIND = "service"
NAME = "tester"
ENTRYPOINT = "service.py"
RUNS = "run_tester_service"

DEPENDS = [
    {"pip": "litellm", "min": "1.0"},
    {"bin": "docker"},
]
