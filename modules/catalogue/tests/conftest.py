"""
conftest.py - Configuration pytest pour les tests de modules/catalogue.
"""

import sys
import os

# Ajouter le chemin parent (modules/) et la racine du projet au sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
