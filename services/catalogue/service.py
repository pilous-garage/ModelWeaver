#!/usr/bin/env python3
"""Service `catalogue` — serveur HTTP de synchro du catalogue.

Wrapper runnable. Le code vit encore dans sql/catalogue_server.py (dépendance
déclarée) ; il sera re-homé ici lors de la mise à jour du bundling/superviseur.
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "projetclient"))
from sql.catalogue_server import main


if __name__ == "__main__":
    main()
