"""adress — table RAM d'adresses COMPLÈTES (clé en clair), au run seulement.

Phase B du domaine info_llm : la table statique `endpoint_apikeytype_model_adress`
(HDD, type-level, AUCUN secret) est résolue contre le vault keyring
(KeyManager) pour produire, EN RAM, les adresses utilisables :

    endpoint_apikeytype_model_adress (HDD)  ×  clés du vault (keyring)
            └──────────────►  adress (RAM, :memory:)  ← 1 par (schéma × clé)

RÈGLES :
  - `adress` est créée/détruite AU RUN, JAMAIS écrite sur HDD (la clé en
    clair ne touche pas le disque).
  - au démarrage : build() injecte toutes les adresses résolues ;
  - pendant le run : add_key()/remove_key() tiennent la table à jour si
    une clé est ajoutée/supprimée (le KeyManager notifie le runtime).
  - appariement clé ↔ api_key_type : voir _key_matches().

Usage :
    from modules.sqlite.info_llm.adress import AdressTable
    rt = Db(":memory:", mode="w", write_token="adress_ram")
    at = AdressTable(rt, info_db, km); at.build()
    # au run : km.set_key(...) -> at.add_key(ref) ; km.delete_key -> at.remove_key(ref)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db
from modules.sqlite.keys import KeyManager

_ADRESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS adress (
    adress_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id        INTEGER,
    endpoint_ref       TEXT DEFAULT '',
    endpoint_url       TEXT DEFAULT '',
    api_key_type       TEXT DEFAULT '',
    provider_id        INTEGER,
    provider_ref       TEXT DEFAULT '',
    model_endpoint_id  INTEGER,
    model_id           INTEGER,
    model_key          TEXT DEFAULT '',
    provider_model_name TEXT DEFAULT '',
    sdk                TEXT DEFAULT '',
    api_key            TEXT DEFAULT '',     -- CLÉ EN CLAIR (RAM seulement)
    api_key_ref        TEXT DEFAULT '',     -- ref UUID du vault (traçabilité)
    key_tag            TEXT DEFAULT '',      -- tag réel de la clé (free/paid)
    health_status      TEXT DEFAULT '',
    available          INTEGER DEFAULT 1,
    deprecated         INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_adress_pk ON adress(provider_ref, api_key_type);
CREATE INDEX IF NOT EXISTS idx_adress_mk ON adress(model_key);
"""


def _key_matches(tag: Optional[str], api_key_type: str) -> bool:
    """Appariement type de clé ↔ api_key_type de la matrice.

    - 'unknown' : on ne connaît pas le type du provider → toute clé OK.
    - 'default' : la clé par défaut (toute clé non-free).
    - 'free'    : seulement les clés tag 'free'.
    - types riches (thinking/plus/premium/max/min/high/low…) : best-effort,
      toute clé non-free (à affiner quand le vault portera api_key_type)."""
    tag = tag or "paid"
    if api_key_type == "unknown":
        return True
    if api_key_type == "free":
        return tag == "free"
    if api_key_type == "default":
        return tag != "free"
    return tag != "free"


def _resolve_row(s: Dict[str, Any], k: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "endpoint_id": s.get("endpoint_id"),
        "endpoint_ref": s.get("endpoint_ref", ""),
        "endpoint_url": s.get("endpoint_url", ""),
        "api_key_type": s.get("api_key_type", ""),
        "provider_id": s.get("provider_id"),
        "provider_ref": s.get("provider_ref", ""),
        "model_endpoint_id": s.get("model_endpoint_id"),
        "model_id": s.get("model_id"),
        "model_key": s.get("model_key", ""),
        "provider_model_name": s.get("provider_model_name", ""),
        "sdk": s.get("sdk", ""),
        "api_key": k.get("api_key", ""),
        "api_key_ref": k.get("ref", ""),
        "key_tag": k.get("tag", ""),
        "health_status": k.get("health_status", ""),
        "available": 1, "deprecated": 0,
    }


class AdressTable:
    """Gère la table RAM `adress` (construite depuis info_llm × vault)."""

    def __init__(self, rt: Db, info_db: Db, km: KeyManager):
        self.rt = rt
        self.info = info_db
        self.km = km
        self.rt.create(1, [_ADRESS_SCHEMA])

    def build(self) -> int:
        """Injecte toutes les adresses résolues (schéma × clés)."""
        self.km.load()
        keys = self.km.list_keys()
        by_prov: Dict[str, List[Dict]] = {}
        for k in keys:
            by_prov.setdefault(k.get("provider_ref", ""), []).append(k)
        n = 0
        rows = self.info.table("endpoint_apikeytype_model_adress").select()
        for s in rows:
            for k in by_prov.get(s.get("provider_ref", ""), []):
                if not _key_matches(k.get("tag"), s.get("api_key_type", "")):
                    continue
                full = self.km.get_key_by_ref(k["ref"])
                if not full:
                    continue
                self.rt.table("adress").add(_resolve_row(s, full))
                n += 1
        return n

    def add_key(self, key_ref: str) -> int:
        """Injecte les adresses pour une clé ajoutée au vault (pendant run).

        Idempotent : ne duplique pas une adresse déjà présente pour ce
        (api_key_ref, model_endpoint_id)."""
        k = self.km.get_key_by_ref(key_ref)
        if not k:
            return 0
        rows = self.info.table("endpoint_apikeytype_model_adress").select(
            where={"provider_ref": k.get("provider_ref", "")})
        n = 0
        for s in rows:
            if not _key_matches(k.get("tag"), s.get("api_key_type", "")):
                continue
            if self.rt.table("adress").get(
                    {"api_key_ref": key_ref,
                     "model_endpoint_id": s.get("model_endpoint_id")}):
                continue
            self.rt.table("adress").add(_resolve_row(s, k))
            n += 1
        return n

    def remove_key(self, key_ref: str) -> int:
        """Supprime les adresses d'une clé retirée du vault (pendant run)."""
        return self.rt.table("adress").remove(where={"api_key_ref": key_ref})

    def query(self, **where) -> List[Dict[str, Any]]:
        return self.rt.table("adress").select(where=where or None)
