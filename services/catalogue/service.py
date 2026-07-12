#!/usr/bin/env python3
"""Service `catalogue` — serveur HTTP de synchro du catalogue (modules.sql.catalogue_server)."""
import sys
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from modules.sql.catalogue_server import main


if __name__ == "__main__":
    main()
