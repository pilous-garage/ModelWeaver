"""Service REST et CLI pour la rotation des clés API."""

from typing import Optional, Dict, Any
from modules.key_rotation.rotation_module import KeyRotationService
from modules.sql.db import ModelWeaverDB

class KeyRotationREST:
    """Endpoints REST pour la gestion de la rotation."""

    @staticmethod
    def manual_rotate(key_ref: Optional[str] = None, strategy: Optional[str] = None) -> Dict[str, Any]:
        service = KeyRotationService()
        if key_ref:
            res = service.rotate_key(key_ref)
            return res
        elif strategy:
            # Rotation pour toutes les clés correspondant à une stratégie donnée
            db = ModelWeaverDB()
            keys = db.keys.list_all()
            results = []
            for k in keys:
                meta = {}
                try:
                    import json
                    meta = json.loads(k.get("metadata_json") or "{}")
                except Exception:
                    pass
                if meta.get("strategy") == strategy or strategy == "all":
                    res = service.rotate_key(k["ref"])
                    results.append({"ref": k["ref"], "result": res})
            return {"status": "ok", "rotated": results}
        else:
            # Tout faire tourner ou échec
            res = service.check_and_rotate_expired()
            return res

    @staticmethod
    def trigger_grace_cleanup() -> Dict[str, Any]:
        service = KeyRotationService()
        return service.revoke_expired_grace_periods()
