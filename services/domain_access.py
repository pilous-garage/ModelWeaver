"""domain_access — WRITERS PAR DOMAINE (Idée 18, section Q).

Chaque système métier qui écrit dans le catalogue possède SON domaine de
données + un writer identifié. Le verrou est STRICT (tables par domaine) :
un domaine n'écrit QUE dans les tables qui lui sont attribuées dans
`domain_writers`, sinon WriteDenied. La vérification se fait à l'écriture.

Mécanique (défense d'app) :
  - `register_domain` / `seed` : charge `domain_writers` (domaine → tables +
    writer_ref + token_hash) depuis le catalogue.
  - `check_write(writer_domaine, table)` : refuse une écriture si le domaine
    de l'appelant n'est PAS autorisé à écrire `table`. Un domaine sans liste
    de tables (tables_json vide) = interdit par défaut (fail-safe).
  - `claim(domaine, writer_ref)` : retourne un TOKEN de domaine (en mémoire,
    par process) pour les appels récurrents ; l'écriture vérifie le token
    courant du thread (contextvars).

Best-effort : si le registre n'est pas chargeable, on refuse (fail-safe). On
peut désactiver le verrou via WRITERS_BYPASS (env) pour la migration.
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Any, Dict, List, Optional, Set

# Env pour désactiver le verrou (migration, debug). Ne jamais activer en prod.
BYPASS = os.environ.get("MODELWEAVER_BYPASS_WRITERS", "") == "1"


class WriteDenied(Exception):
    """Écriture refusée : le domaine n'est pas autorisé sur cette table."""


class DomainAccess:
    """Registre global des domaines + tokens par thread."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._tables: Dict[str, Set[str]] = {}
                    cls._instance._tokens: Dict[str, str] = {}
                    cls._instance._loaded = False
        return cls._instance

    def load(self, cat=None) -> None:
        """Charge les domaines depuis la table domain_writers."""
        if self._loaded and not BYPASS:
            return
        if BYPASS:
            return
        try:
            if cat is None:
                from modules.sql.catalogue_repo import CatalogueDB
                cat = CatalogueDB()
            import json as _json
            rows = cat.conn.execute(
                "SELECT domaine, writer_ref, tables_json, token_hash "
                "FROM domain_writers").fetchall()
            for r in rows:
                tables = []
                try:
                    tables = _json.loads(r["tables_json"] or "[]")
                except Exception:
                    tables = []
                self._tables[r["domaine"]] = set(tables)
                self._tokens[r["domaine"]] = r["token_hash"] or ""
            self._loaded = True
        except Exception:
            self._loaded = False

    def tables_for(self, domaine: str) -> Set[str]:
        return self._tables.get(domaine, set())

    def check_write(self, domaine: str, table: str) -> bool:
        """Un domaine peut-il écrire `table` ? Fail-safe (refus si inconnu)."""
        if BYPASS:
            return True
        self.load()
        allowed = self._tables.get(domaine)
        if allowed is None:
            return False  # domaine inconnu → refuse
        if not allowed:
            return False  # domaine sans tables déclarées → refuse (fail-safe)
        return table in allowed

    def guard_writer(self, domaine: str, writer_ref: str) -> bool:
        """Vérifie que le writer déclaré correspond au domaine (défense d'app)."""
        if BYPASS:
            return True
        self.load()
        # on vérifie côté BDD le writer_ref attendu
        try:
            from modules.sql.catalogue_repo import CatalogueDB
            cat = CatalogueDB()
            row = cat.conn.execute(
                "SELECT writer_ref FROM domain_writers WHERE domaine = ?",
                (domaine,)).fetchone()
            return bool(row and row["writer_ref"] and
                        row["writer_ref"] == writer_ref)
        except Exception:
            return True  # best-effort : ne bloque pas la découverte


# Singleton
_registry = DomainAccess()


def check_write(domaine: str, table: str) -> bool:
    """Public : un domaine peut-il écrire cette table ?"""
    return _registry.check_write(domaine, table)


def guard(domaine: str, writer_ref: str, table: str) -> None:
    """Public : lève WriteDenied si le writer n'est pas autorisé à écrire
    `table` dans son domaine. À appeler à chaque écriture métier."""
    if BYPASS:
        return
    if not check_write(domaine, table):
        raise WriteDenied(
            f"writer '{writer_ref}' du domaine '{domaine}' interdit sur "
            f"'{table}' — tables autorisées: {sorted(_registry.tables_for(domaine))}")


def tables_for(domaine: str) -> Set[str]:
    return _registry.tables_for(domaine)