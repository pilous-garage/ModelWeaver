"""key_manager — domaine de clés API sécurisé (keyring OS + fallback Fernet).

REPLIQUE DU DESIGN LEGACY (modules/key_manager) mais backend sqlite via
modules/sqlite (l'ancien backend modules/sql ayant été retiré). La DB ne
stocke QUE les métadonnées + un `ref` UUID ; les secrets vivent dans le
keyring OS (ou un fichier Fernet chiffré en headless). AUCUNE clé en clair
sur disque — seulement en RAM après load().

Usage (Phase B) :
    from modules.sqlite.keys import KeyManager
    km = KeyManager(); km.load()
    km.set_key("openai", "sk-...", tag="paid")
    row = km.get_key_by_ref(ref)   # dict avec 'api_key' (clair, RAM)
"""
from __future__ import annotations

import os
import json
import base64
from pathlib import Path
from typing import Any, Dict, List, Optional

import keyring

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path
from services._common import mw_home

_MW_DIR = mw_home()
_KEYRING_SERVICE = "modelweaver"
_KEYRING_TABLE_KEY = "keys_table"
_FALLBACK_KEY = _MW_DIR / ".keyring_fallback.key"
_FALLBACK_KEY_ENC = _MW_DIR / ".keyring_fallback.key.enc"
_FALLBACK_STORE = _MW_DIR / ".keyring_fallback.json"

WRITE_KEYS_TOKEN = "write_keys"


def _mask_key(key: str) -> str:
    if len(key) <= 4:
        return "****"
    return key[:2] + "****" + key[-2:]


def _machine_secret() -> bytes:
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            mid = Path(p).read_text().strip()
            if mid:
                return mid.encode()
        except Exception:
            pass
    import uuid
    return str(uuid.getnode()).encode()


class KeyLockedError(Exception):
    """Levée quand on lit EN CLAIR une clé verrouillée (masque OK)."""


class _KeyStore:
    """Backend sécurisé : keyring OS (table unique) ou fichier Fernet local."""

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
        if _FALLBACK_KEY_ENC.exists():
            from getpass import getpass
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            try:
                payload = json.loads(_FALLBACK_KEY_ENC.read_text())
            except Exception:
                payload = {}
            salt = base64.urlsafe_b64decode(payload.get("salt", ""))
            nonce = base64.urlsafe_b64decode(payload.get("nonce", ""))
            ct = base64.urlsafe_b64decode(payload.get("ct", ""))
            password = getpass("Mot de passe du keyring fallback : ").encode()
            try:
                kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32,
                                 salt=salt or os.urandom(16), iterations=200_000)
                key = AESGCM(kdf.derive(password)).decrypt(nonce, ct, None)
            except Exception:
                raise RuntimeError("Mot de passe invalide ou fichier corrompu.")
        elif _FALLBACK_KEY.exists():
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
        self._fernet = __import__("cryptography.fernet",
                                  fromlist=["Fernet"]).Fernet(key)
        if _FALLBACK_STORE.exists():
            try:
                self._cache = json.loads(
                    self._fernet.decrypt(_FALLBACK_STORE.read_bytes()))
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
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_TABLE_KEY,
                                 json.dumps(table))
        else:
            self._cache = table
            _FALLBACK_STORE.write_bytes(
                self._fernet.encrypt(json.dumps(table).encode()))
            os.chmod(_FALLBACK_STORE, 0o600)


def _load_schema() -> list[str]:
    import os
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "keys_schema.sql")) as f:
        return [f.read()]


_KEYS_SCHEMA = _load_schema()


def _ref() -> str:
    import uuid
    return str(uuid.uuid4())


class KeyManager:
    """Gestionnaire de clés API (keyring + métadonnées sqlite)."""

    def __init__(self, db: Optional[Db] = None, store: Optional[_KeyStore] = None):
        self.db = db or Db(db_path("keys"), mode="w", write_token="")
        self.db.create(1, _KEYS_SCHEMA)
        self.store = store or _KeyStore()
        self._cache: Dict[str, str] = {}
        self._loaded = False

    # ── cycle de vie ──
    def load(self) -> None:
        self._cache = self.store.load_all()
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # ── écriture ──
    def set_key(self, provider_ref: str, api_key: str,
                api_base: Optional[str] = None,
                identity: str = "default", tag: str = "paid",
                grade: Optional[str] = None,
                metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
        meta = dict(metadata or {})
        if api_base:
            meta["api_base"] = api_base
        meta_json = str(meta) if meta else None
        # une clé par (provider, identity, tag) : un provider peut avoir
        # plusieurs clés (paid + free, comptes distincts…)
        existing = self.db.table("api_keys").get(
            {"provider_ref": provider_ref, "identity": identity, "tag": tag})
        if existing:
            ref = existing["ref"]
            self.db.table("api_keys").update(
                {"ref": ref},
                {"tag": tag, "grade": grade, "metadata_json": meta_json})
        else:
            ref = _ref()
            self.db.table("api_keys").add({
                "ref": ref, "provider_ref": provider_ref,
                "identity": identity, "tag": tag, "grade": grade,
                "health_status": "unknown", "locked": 0,
                "metadata_json": meta_json})
        self.store.set(ref, api_key)
        self._cache[ref] = api_key
        self._loaded = True
        return ref

    # ── lecture ──
    def get_key_by_ref(self, ref: str) -> Optional[Dict[str, Any]]:
        self._ensure_loaded()
        key = self.db.table("api_keys").get({"ref": ref})
        if not key:
            return None
        if key.get("locked"):
            raise KeyLockedError(f"Clé {ref} verrouillée")
        if key.get("health_status") not in (None, "unknown", "ok", "degraded"):
            return None
        api_key = self._cache.get(ref)
        if not api_key:
            return None
        return {
            "ref": ref, "api_key": api_key,
            "provider_ref": key["provider_ref"], "tag": key["tag"],
            "grade": key["grade"], "identity": key["identity"],
            "health_status": key["health_status"],
        }

    def list_keys(self, identity: Optional[str] = None,
                  tag: Optional[str] = None,
                  provider_ref: Optional[str] = None) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        w: Dict[str, Any] = {}
        if identity:
            w["identity"] = identity
        if provider_ref:
            w["provider_ref"] = provider_ref
        rows = self.db.table("api_keys").select(where=w or None)
        out = []
        for r in rows:
            if tag and r.get("tag") != tag:
                continue
            d = dict(r)
            ak = self._cache.get(r.get("ref"), "")
            d["key_display"] = _mask_key(ak) if ak else "****"
            out.append(d)
        return out

    def delete_key(self, ref: str) -> bool:
        self.store.delete(ref)
        self._cache.pop(ref, None)
        return bool(self.db.table("api_keys").remove({"ref": ref}))

    def list_providers(self) -> List[str]:
        return list({k["provider_ref"] for k in self.list_keys()})

    def update_health(self, ref: str, status: str) -> None:
        self.db.table("api_keys").update({"ref": ref},
                                         {"health_status": status})
