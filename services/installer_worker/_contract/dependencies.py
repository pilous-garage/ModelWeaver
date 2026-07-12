"""Contrat des DEPENDANCES du service `installer_worker`."""

CONSUMES = {
    'gui_helper': ['run_installer_service'],
}
