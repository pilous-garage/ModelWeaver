"""Contrat PUBLIC du module `sql` : couche d'accès aux données (data-layer).

Unité foundation migrée à la racine du repo (et NON sous modules/) afin de
préserver le chemin d'import historique `modules.sql.db` / `modules.sql.catalogue_server` : ~40
sites l'importent tel quel. Reste un *module* (pas un service) — la concurrence
d'écriture est gérée par SQLite (WAL + busy_timeout), cf. CARNET (idée RBAC
data-layer différée).
"""

KIND = "module"
NAME = "sql"
MODULE = "modules.sql.db"
EXPORTS = ['ModelWeaverDB', 'CatalogueDB', 'TursoCatalogueDB']
