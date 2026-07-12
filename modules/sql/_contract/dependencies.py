"""Contrat des DEPENDANCES du module `sql`.

Unité de base (data-layer) : ne consomme aucune autre unité-projet
(modules./services.). Ses seuls imports internes (`modules.sql.migrations`,
`modules.sql.agent_repository`, `modules.sql.orchestration_repository`) sont intra-unité et
donc hors périmètre CONSUMES.
"""

CONSUMES = {}
