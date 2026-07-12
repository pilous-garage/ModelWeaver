"""Contrat PUBLIC du service `api` : ce qui est accessible depuis l'extérieur.

Source de vérité du contrat multi-langage (GUI TS / CLI Python / Rust parlent
tous ces routes). Vérifié par hardcheck contre la table ROUTES réelle du daemon.
"""

KIND = "service"
NAME = "api"

# Table de routes réellement servie, désignée par "module:attribut".
ROUTES_SOURCE = "daemon:ROUTES"

# Point d'entrée du service (supervisé). Un seul daemon à la fois (lock api).
RUNS = "serve"

# Routes exposées -> paramètres attendus ([] si aucun ; params optionnels tolérés).
EXPOSES = {
    # A. Système & environnement
    "system/info": [],
    "system/deps/check": [],
    "system/state/get": [],
    "system/state/save": [],
    # B. Bases
    "db/init": [],
    "db/check": [],
    # C. Catalogue
    "catalogue/tools/list": [],
    "catalogue/seed": [],
    "catalogue/sync": ["url"],
    "catalogue/tools_table/update": [],
    # D. Outils installés (synchrone)
    "tools/installed/list": [],
    "tools/install": ["ref"],
    "tools/uninstall": ["ref"],
    # E. File de jobs (asynchrone)
    "jobs/add": ["ref", "job_type"],
    "jobs/list": [],
    "jobs/status": ["id"],
    "jobs/cancel": ["id"],
    "jobs/clear": [],
    # H. Logs
    "logs/read": [],
    "logs/write": ["level", "message"],
}
