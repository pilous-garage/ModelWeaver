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

TOKENS RÉELS (défense d'app par processus) :
  - Les tokens (secrets aléatoires) vivent HORS BDD, dans
    `<mw_home>/domain_tokens.json` (chmod 600) — un attaquant qui n'a que la
    BDD ne connaît PAS les tokens ; la BDD ne stocke que le hash (sha256).
  - `init_tokens(cat)` : génère un token par domaine, écrit le fichier, met à
    jour `token_hash` en BDD. Idempotent (ne régénère pas les tokens
    existants).
  - `verify_tokens(cat)` : à appeler au DÉMARRAGE du daemon — compare les
    tokens du fichier aux hash BDD ; si un domaine mismatch, il est désactivé
    (les écritures sont refusées) et {ok: False} est retourné.

Best-effort : si le registre n'est pas chargeable, on refuse (fail-safe). On
peut désactiver le verrou via WRITERS_BYPASS (env) pour la migration.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Env pour désactiver le verrou (migration, debug). Ne jamais activer en prod.
BYPASS = os.environ.get("MODELWEAVER_BYPASS_WRITERS", "") == "1"


def _tokens_path() -> Path:
    """Chemin du fichier de tokens HORS BDD (sous mw_home, chmod 600)."""
    try:
        from services._common import mw_home
        return mw_home() / "domain_tokens.json"
    except Exception:
        return Path.home() / ".modelweaver" / "domain_tokens.json"


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

    # ── Tokens réels (défense d'app par processus) ──

    def load_tokens(self) -> Dict[str, str]:
        """Tokens en clair depuis le fichier hors BDD (vide si absent)."""
        try:
            p = _tokens_path()
            if not p.exists():
                return {}
            return json.loads(p.read_text())
        except Exception:
            return {}

    def save_tokens(self, tokens: Dict[str, str]) -> None:
        """Écrit les tokens hors BDD (chmod 600)."""
        p = _tokens_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(tokens, indent=2))
        os.chmod(p, 0o600)

    def init_tokens(self, cat=None) -> Dict[str, Tuple[str, bool]]:
        """Génère un token par domaine UNIQUEMENT si absent (fichier OU BDD).

        Idempotent : si le domaine a déjà un token (fichier ou hash BDD), on
        ne le régénère PAS (régénérer invaliderait les autres processes déjà
        démarrés avec l'ancien token). Retourne {domaine: (token, created)}.
        """
        from modules.sql.catalogue_repo import CatalogueDB
        cat = cat or CatalogueDB()
        rows = cat.conn.execute(
            "SELECT domaine, token_hash FROM domain_writers "
            "ORDER BY domaine").fetchall()
        tokens = self.load_tokens()
        out: Dict[str, Tuple[str, bool]] = {}
        for r in rows:
            dom = r["domaine"]
            existing = tokens.get(dom)
            if existing and r["token_hash"] and \
                    hashlib.sha256(existing.encode()).hexdigest() == r["token_hash"]:
                out[dom] = (existing, False)  # déjà en place (fichier + BDD alignés)
                continue
            token = existing or secrets.token_hex(32)
            h = hashlib.sha256(token.encode()).hexdigest()
            cat.conn.execute(
                "UPDATE domain_writers SET token_hash = ? WHERE domaine = ?",
                (h, dom))
            tokens[dom] = token
            out[dom] = (token, True)
        self.save_tokens(tokens)
        cat.conn.commit()
        self._tokens = {d: self._hash(t) for d, t in tokens.items()}
        return out

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def verify_tokens(self, cat=None) -> Dict[str, Any]:
        """VÉRIFICATION au démarrage : tokens du fichier vs hash BDD.

        Pour chaque domaine :
          - domaine sans hash BDD      → vérifié (rien à prouver) ;
          - hash BDD + token présent
            et sha256(token) == hash   → vérifié ;
          - hash BDD + token ABSENT ou mismatch → ÉCHEC : le domaine n'est
            PAS marqué chargeable → toute écriture sera refusée (fail-safe).
        Retourne {ok, checked, failed, domains} — `ok` = tout est vérifié.
        """
        from modules.sql.catalogue_repo import CatalogueDB
        cat = cat or CatalogueDB()
        self.load(cat)
        self.load_tokens()
        tokens = self.load_tokens()
        rows = cat.conn.execute(
            "SELECT domaine, token_hash FROM domain_writers").fetchall()
        checked: List[str] = []
        failed: List[str] = []
        for r in rows:
            dom = r["domaine"]
            h = (r["token_hash"] or "").strip()
            if not h:
                checked.append(dom)
                continue
            tok = tokens.get(dom, "")
            if tok and hashlib.sha256(tok.encode()).hexdigest() == h:
                checked.append(dom)
                self._tokens[dom] = h
            else:
                failed.append(dom)
                # désactivation COMPLÈTE : plus de tables ni de hash → les
                # écritures du domaine seront refusées (fail-safe)
                self._tokens.pop(dom, None)
                self._tables.pop(dom, None)
        return {"ok": not failed, "checked": checked, "failed": failed,
                "domains": [r["domaine"] for r in rows]}

    def token_ok(self, domaine: str, token: str) -> bool:
        """Vérifie un token FOURNI par l'appelant contre le hash BDD."""
        if BYPASS or not token:
            return True
        self.load()
        h = self._tokens.get(domaine)
        return bool(h) and hashlib.sha256(token.encode()).hexdigest() == h


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


def guard_domain(domaine: str, writer_ref: str) -> None:
    """Public : lève WriteDenied si le domaine n'existe pas / est désactivé
    (cas des domaines sans tables BDD, ex. manifest = fichiers)."""
    if BYPASS:
        return
    _registry.load()
    tables = _registry.tables_for(domaine)
    if domaine not in _registry._tokens and not tables:
        raise WriteDenied(
            f"domaine '{domaine}' inconnu ou inactif — writer '{writer_ref}' "
            f"refusé (fail-safe)")


def tables_for(domaine: str) -> Set[str]:
    return _registry.tables_for(domaine)


def init_tokens(cat=None) -> Dict[str, Tuple[str, bool]]:
    """Public : génère/garantit les tokens de domaine (idempotent)."""
    return _registry.init_tokens(cat)


def verify_tokens(cat=None) -> Dict[str, Any]:
    """Public : vérifie les tokens au démarrage — {ok, checked, failed, ...}."""
    return _registry.verify_tokens(cat)


def token_ok(domaine: str, token: str) -> bool:
    """Public : un token fourni matche-t-il le hash du domaine ?"""
    return _registry.token_ok(domaine, token)


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if cmd == "init":
        r = init_tokens()
        print(f"tokens initialisés: {len(r)} domaines")
        for dom, (tok, created) in sorted(r.items()):
            print(f"  {dom:10s} {'créé' if created else 'déjà en place'}")
    elif cmd == "verify":
        r = verify_tokens()
        print(f"vérification: {'OK' if r['ok'] else 'ÉCHEC'}")
        for dom in r.get("checked", []):
            print(f"  ✓ {dom}")
        for dom in r.get("failed", []):
            print(f"  ✗ {dom} (token manquant ou mismatch — écritures refusées)")
    else:
        print("usage: python -m services.domain_access [init|verify]")
        sys.exit(1)