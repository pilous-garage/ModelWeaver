"""Contrat des DEPENDANCES du service `catalogue`."""

CONSUMES = {
    'modules.sql.catalogue_server': ['main'],
    'services.depends': ['require_deps'],
}
