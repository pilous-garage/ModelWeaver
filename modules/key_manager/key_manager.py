from typing import Dict, Any, Optional, List

from modules.sql.db import ModelWeaverDB


class KeyManager:
    """Gestionnaire de clés API.

    Les clés sont stockées via modules.sql.db — aucun fichier JSON manipulé ici.
    """

    def __init__(self, db: Optional[ModelWeaverDB] = None):
        self.db = db or ModelWeaverDB()

    def set_key(self, provider_ref: str, api_key: str,
                api_base: Optional[str] = None,
                identity: str = "default",
                tag: str = "paid",
                grade: Optional[str] = None,
                metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Ajoute ou met à jour une clé pour un fournisseur."""
        prov = self.db.providers.get(provider_ref)
        if not prov:
            self.db.providers.save({"ref": provider_ref, "name": provider_ref,
                                     "provider_type": "cloud"})
            prov = self.db.providers.get(provider_ref)
            if not prov:
                return None

        meta = dict(metadata or {})
        if api_base:
            meta["api_base"] = api_base

        ref = self.db.keys.save({
            "provider_id": prov["id"],
            "key_value": api_key,
            "identity": identity,
            "tag": tag,
            "grade": grade,
            "metadata_json": str(meta) if meta else None,
        })
        self.db.commit()
        return ref

    def get_key(self, provider_ref: str,
                identity: str = "default") -> Optional[Dict[str, Any]]:
        """Récupère la meilleure clé disponible pour un fournisseur."""
        key = self.db.keys.get_for_provider(provider_ref, identity)
        if key:
            return {
                "api_key": key["key_value"],
                "api_base": self._extract_base(key),
                "metadata": self._extract_meta(key),
                "tag": key["tag"],
                "grade": key["grade"],
                "health_status": key["health_status"],
            }
        return None

    def get_key_by_ref(self, ref: str) -> Optional[Dict[str, Any]]:
        key = self.db.keys.get(ref)
        if key:
            return {
                "api_key": key["key_value"],
                "api_base": self._extract_base(key),
                "metadata": self._extract_meta(key),
                "tag": key["tag"],
                "grade": key["grade"],
                "health_status": key["health_status"],
                "provider_ref": key.get("provider_ref"),
            }
        return None

    def delete_key(self, ref: str) -> bool:
        ok = self.db.keys.delete(ref)
        self.db.commit()
        return ok

    def list_keys(self, identity: Optional[str] = None,
                  tag: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.keys.list_all(identity=identity, tag=tag)

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
            import json
            try:
                return json.loads(meta).get("api_base")
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    def _extract_meta(self, key: Dict) -> Dict:
        meta = key.get("metadata_json")
        if meta:
            import json
            try:
                d = json.loads(meta)
                d.pop("api_base", None)
                return d
            except (json.JSONDecodeError, TypeError):
                pass
        return {}
