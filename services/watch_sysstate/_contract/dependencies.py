"""Contrat des DEPENDANCES du service `watch_sysstate`."""

CONSUMES = {
    'gui_helper': ['watch_system_state'],
}
