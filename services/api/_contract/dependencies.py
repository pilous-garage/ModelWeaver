"""Contrat des DEPENDANCES du service `api` : fonctions/classes externes consommées.

Vérifié par hardcheck : chaque symbole doit exister dans l'unité source, et le
service ne doit pas consommer d'autre symbole externe non déclaré ici.
"""
from services.installer_worker.jobs import (
    enqueue_job, job_status, list_jobs, cancel_job, clear_jobs,
    install_tool, uninstall_tool,
)
from modules.sql.db import ModelWeaverDB, CatalogueDB
from modules.checker.checker import Checker

CONSUMES = {
    # File d'installation + install/uninstall réels.
    "services.installer_worker.jobs": [
        "enqueue_job", "job_status", "list_jobs", "cancel_job", "clear_jobs",
        "install_tool", "uninstall_tool",
    ],
    # Sous-modules consommés directement par le daemon.
    "services.installer_worker": ["jobs"],
    "services.watch_sysstate": ["service"],
    # Data-layer.
    "modules.sql.db": ["ModelWeaverDB", "CatalogueDB"],
    # Inspection système.
    "modules.checker.checker": ["Checker"],
    # Helpers partagés.
    "services._common": ["_db_paths", "_quiet_stdout", "log_to_file", "acquire_instance_lock"],
    # Vérification des dépendances.
    "services.depends": ["check_all_units"],
}
