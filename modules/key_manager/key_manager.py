"""Key Manager — Gestion des clés API via le keyring OS.

Sécurité au repos uniquement (un dump RAM expose toujours les clés — inherent
à tout agent qui doit les utiliser en clair). Rien n'est stocké en clair sur le
disque :

  - Le keyring OS (GNOME Keyring / macOS Keychain / Windows Credential Manager)
    détient UNE entrée « modelweaver / keys_table » = JSON {ref: api_key}.
    Chiffré au repos, lié à la session utilisateur.
  - La DB SQLite ne contient QUE les métadonnées (provider, tag, grade, santé,
    timestamps) + un `ref` (UUID unique, jamais réécrit) faisant le lien avec
    l'entrée keyring. Aucune clé, aucun masque ne touche le disque.
  - `key_display` (ab****cd) est dérivé EN MÉMOIRE depuis le cache chargé après
    validation du keyring (load()).

Fallback headless (serveur sans D-Bus) : même structure mais fichier chiffré
Fernet dans ~/.modelweaver/, clé dérivée du machine-id.
"""

import os
import json
import base64
from pathlib import Path
from typing import Dict, Any, Optional, List

import keyring

from modules.sql.db import ModelWeaverDB
from services._common import mw_home

_MW_DIR = mw_home()
_KEYRING_SERVICE = "modelweaver"
_KEYRING_TABLE_KEY = "keys_table"
_FALLBACK_KEY = _MW_DIR / ".keyring_fallback.key"
_FALLBACK_STORE = _MW_DIR / ".keyring_fallback.json"


class KeyLockedError(Exception):
    """Levée quand on tente d'obtenir la clé EN CLAIR d'une clé verrouillée.

    N'empêche PAS l'affichage du masque (key_display) ni des métadonnées — seul
    l'accès à la valeur plaintext est refusé.
    """


def _mask_key(key: str) -> str:
    if len(key) <= 4:
        return "****"
    return key[:2] + "****" + key[-2:]


def _machine_secret() -> bytes:
    """Secret stable par machine pour dériver la clé du fallback."""
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            mid = Path(p).read_text().strip()
            if mid:
                return mid.encode()
        except Exception:
            pass
    import uuid
    return str(uuid.getnode()).encode()


class _KeyStore:
    """Backend sécurisé : keyring OS (table unique) ou fichier Fernet local.

    La table entière {ref: api_key} est stockée en un seul endroit. Le `ref`
    est l'ID unique non rééritable généré par le KeyManager.
    """

    def __init__(self):
        self._use_keyring = True
        self._fernet = None
        self._cache: Dict[str, str] = {}
        try:
            keyring.get_password(_KEYRING_SERVICE, _KEYRING_TABLE_KEY)
        except Exception:
            self._use_keyring = False
            self._init_fallback()

    def _init_fallback(self):
        _MW_DIR.mkdir(parents=True, exist_ok=True)
        if _FALLBACK_KEY.exists():
            key = _FALLBACK_KEY.read_bytes()
        else:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            salt = os.urandom(16)
            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                             salt=salt, iterations=100000)
            key = base64.urlsafe_b64encode(kdf.derive(_machine_secret()))
            _FALLBACK_KEY.write_bytes(key)
            os.chmod(_FALLBACK_KEY, 0o600)
        self._fernet = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet(key)
        if _FALLBACK_STORE.exists():
            try:
                raw = self._fernet.decrypt(_FALLBACK_STORE.read_bytes())
                self._cache = json.loads(raw)
            except Exception:
                self._cache = {}

    def load_all(self) -> Dict[str, str]:
        if self._use_keyring:
            raw = keyring.get_password(_KEYRING_SERVICE, _KEYRING_TABLE_KEY)
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}
        return dict(self._cache)

    def get(self, ref: str) -> Optional[str]:
        return self.load_all().get(ref)

    def set(self, ref: str, value: str) -> None:
        table = self.load_all()
        table[ref] = value
        self._flush(table)

    def delete(self, ref: str) -> None:
        table = self.load_all()
        table.pop(ref, None)
        self._flush(table)

    def _flush(self, table: Dict[str, str]) -> None:
        if self._use_keyring:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_TABLE_KEY, json.dumps(table))
        else:
            self._cache = table
            raw = json.dumps(table).encode()
            _FALLBACK_STORE.write_bytes(self._fernet.encrypt(raw))
            os.chmod(_FALLBACK_STORE, 0o600)


class KeyManager:
    """Gestionnaire de clés API.

    La DB ne stocke que les métadonnées + le `ref` (UUID). Les secrets vivent
    dans le keyring OS. Après validation (load()), le cache en mémoire sert
    toutes les lectures sans re-toucher le disque.
    """

    def __init__(self, db: Optional[ModelWeaverDB] = None, store: Optional[_KeyStore] = None):
        self.db = db or ModelWeaverDB()
        self.store = store or _KeyStore()
        self._cache: Dict[str, str] = {}   # ref -> api_key (en mémoire)
        self._loaded = False

    # ── Cycle de vie : validation keyring → chargement en mémoire ──

    def load(self) -> None:
        """Charge toute la table de clés en mémoire (après validation keyring)."""
        self._cache = self.store.load_all()
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ── Écriture ──

    def set_key(self, provider_ref: str, api_key: str,
                api_base: Optional[str] = None,
                identity: str = "default",
                tag: str = "paid",
                grade: Optional[str] = None,
                metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Ajoute ou met à jour une clé pour un fournisseur.

        Le `ref` (ID unique non rééritable) est réutilisé si une clé existe déjà
        pour ce (provider, identity).
        """
        prov = self.db.providers.get(provider_ref)
        if not prov:
            self.db.providers.save({"ref": provider_ref, "name": provider_ref,
                                     "provider_type": "cloud"})
            prov = self.db.providers.get(provider_ref)
            if not prov:
                return None

        # Réutilise le ref existant (stable, jamais réécrit) — verrouillé ou non
        existing = self.db.keys.get_any_for_provider(provider_ref, identity)
        meta = dict(metadata or {})
        if api_base:
            meta["api_base"] = api_base
        meta_json = str(meta) if meta else None

        if existing:
            ref = existing["ref"]
            self.db.keys.update(ref, tag=tag, grade=grade, metadata_json=meta_json)
        else:
            ref = self.db.keys.save({
                "ref": _ref(),
                "provider_id": prov["id"],
                "key_value": "",   # jamais de clé en clair
                "identity": identity,
                "tag": tag,
                "grade": grade,
                "metadata_json": meta_json,
            })

        self.store.set(ref, api_key)
        self._cache[ref] = api_key
        self._loaded = True
        self.db.commit()
        return ref

    # ── Lecture ──

    def get_key(self, provider_ref: str,
                identity: str = "default") -> Optional[Dict[str, Any]]:
        """Renvoie la clé EN CLAIR. Refusée (KeyLockedError) si verrouillée.

        Distingue « aucune clé » (None) de « clé existante mais verrouillée »
        (KeyLockedError). Le masque et les métadonnées restent accessibles via
        list_keys() même si verrouillée.
        """
        self._ensure_loaded()
        # Existence + état verrou (ignore la santé pour le test de verrou)
        any_key = self.db.keys.get_any_for_provider(provider_ref, identity)
        if not any_key:
            return None
        if any_key.get("locked"):
            raise KeyLockedError(f"Clé {provider_ref} verrouillée")
        # Santé : on ne rend pas une clé morte
        if any_key.get("health_status") not in (None, "unknown", "ok", "degraded"):
            return None
        api_key = self._cache.get(any_key["ref"])
        if not api_key:
            return None
        return {
            "ref": any_key["ref"],
            "api_key": api_key,
            "api_base": self._extract_base(any_key),
            "metadata": self._extract_meta(any_key),
            "tag": any_key["tag"],
            "grade": any_key["grade"],
            "health_status": any_key["health_status"],
        }

    def get_key_by_ref(self, ref: str) -> Optional[Dict[str, Any]]:
        """Renvoie la clé EN CLAIR. Refusée (KeyLockedError) si verrouillée."""
        self._ensure_loaded()
        key = self.db.keys.get(ref)
        if not key:
            return None
        if key.get("locked"):
            raise KeyLockedError(f"Clé {ref} verrouillée")
        api_key = self._cache.get(ref)
        if not api_key:
            return None
        return {
            "api_key": api_key,
            "api_base": self._extract_base(key),
            "metadata": self._extract_meta(key),
            "tag": key["tag"],
            "grade": key["grade"],
            "health_status": key["health_status"],
            "provider_ref": key.get("provider_ref"),
        }

    def is_locked(self, ref: str) -> bool:
        key = self.db.keys.get(ref)
        return bool(key and key.get("locked"))

    def set_lock(self, ref: str, locked: bool) -> bool:
        ok = self.db.keys.set_lock(ref, locked)
        self.db.commit()
        return ok

    def lock_key(self, ref: str) -> bool:
        return self.set_lock(ref, True)

    def unlock_key(self, ref: str) -> bool:
        return self.set_lock(ref, False)

    def delete_key(self, ref: str) -> bool:
        self.store.delete(ref)
        self._cache.pop(ref, None)
        ok = self.db.keys.delete(ref)
        self.db.commit()
        return ok

    def list_keys(self, identity: Optional[str] = None,
                  tag: Optional[str] = None) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        rows = self.db.keys.list_all(identity=identity, tag=tag)
        out = []
        for r in rows:
            d = dict(r)
            d.pop("key_value", None)
            # key_display dérivé EN MÉMOIRE (jamais stocké)
            ak = self._cache.get(r.get("ref"), "")
            d["key_display"] = _mask_key(ak) if ak else "****"
            out.append(d)
        return out

    def list_providers(self) -> List[str]:
        keys = self.db.keys.list_all()
        return list(set(k.get("provider_ref", "") for k in keys if k.get("provider_ref")))

    def update_health(self, ref: str, status: str,
                      error: Optional[str] = None) -> None:
        self.db.keys.update_health(ref, status, error)
        self.db.commit()

    def _extract_base(self, key: Dict) -> Optional[str]:
        meta = key.get("metadata_json")
        if meta:
            import json as _j
            try:
                return _j.loads(meta).get("api_base")
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    def _extract_meta(self, key: Dict) -> Dict:
        meta = key.get("metadata_json")
        if meta:
            import json as _j
            try:
                d = _j.loads(meta)
                d.pop("api_base", None)
                return d
            except (json.JSONDecodeError, TypeError):
                pass
        return {}


def _ref() -> str:
    import uuid
    return "key_" + uuid.uuid4().hex[:12]
