import os
import time
import secrets
import hashlib
import json
import logging
from typing import Optional, Dict, List

from modules.sql.db import ModelWeaverDB
from services.key_manager import set_key, get_key, delete_key
from services.logger import get_logger

logger = get_logger("key_rotation")

class KeyRotationService:
    """Service de rotation automatique et manuelle des clés API."""

    def __init__(self, db: Optional[ModelWeaverDB] = None):
        self.db = db or ModelWeaverDB()

    def rotate_key(self, key_ref: str, new_key_material: Optional[str] = None, grace_period_hours: int = 24) -> Dict[str, Any]:
        """Exécute la rotation d'une clé API spécifique.
        
        1) Génère ou utilise le nouveau matériel de clé (sécurisé via secrets.token_urlsafe).
        2) Hash et stocke la nouvelle clé via le key_manager.
        3) Marque l'ancienne clé comme dépréciée (deprecation timestamp dans metadata).
        4) Maintient les deux clés actives pendant la période de grâce (grace period).
        5) Notifie via webhook / pub/sub.
        6) Incrémente les métriques.
        """
        try:
            # Récupérer l'ancienne clé
            old_key_record = self.db.keys.get(key_ref)
            if not old_key_record:
                logger.error(f"Key not found for rotation: {key_ref}")
                return {"status": "error", "message": "Key not found"}

            provider_id = old_key_record["provider_id"]
            identity = old_key_record.get("identity", "default")
            provider_ref = old_key_record.get("provider_ref")

            # 1. Générer de nouveau matériel si non fourni
            if not new_key_material:
                new_key_material = f"sk-rot-{secrets.token_urlsafe(32)}"

            # 2. Hash de la clé pour stockage (SHA256)
            new_key_hash = hashlib.sha256(new_key_material.encode("utf-8")).hexdigest()

            # 3. Stocker la nouvelle clé (via le key_manager standard ou insertion directe avec ref unique)
            # On utilise le même provider_ref et identity, mais un ref unique
            from modules.key_manager.key_manager import KeyManager
            km = KeyManager(db=self.db)
            
            # Metadata pour deprecation et grace period
            now = int(time.time())
            deprecation_ts = now + (grace_period_hours * 3600)
            
            meta_old = json.loads(old_key_record.get("metadata_json") or "{}")
            meta_old["deprecated_at"] = now
            meta_old["grace_until"] = deprecation_ts
            self.db.keys.update(key_ref, metadata_json=json.dumps(meta_old))

            # Créer la nouvelle clé
            new_ref = km.set_key(
                provider_ref=provider_ref,
                api_key=new_key_material,
                identity=identity,
                tag=old_key_record.get("tag", "paid"),
                grade=old_key_record.get("grade"),
                metadata={"rotated_from": key_ref, "created_at": now}
            )

            # 4. Notification webhook / pub/sub
            self._notify_services(provider_ref, key_ref, new_ref)

            logger.info(f"Successfully rotated key {key_ref} -> new ref {new_ref} for provider {provider_ref}")
            return {
                "status": "ok",
                "old_key_ref": key_ref,
                "new_key_ref": new_ref,
                "deprecation_timestamp": deprecation_ts,
                "grace_period_hours": grace_period_hours
            }

        except Exception as e:
            logger.error(f"Failed to rotate key {key_ref}: {str(e)}")
            return {"status": "error", "message": str(e)}

    def check_and_rotate_expired(self):
        """Vérifie périodiquement toutes les clés et déclenche la rotation si nécessaire.
        
        Règle : current_time - last_rotated > rotation_interval_days
        """
        try:
            now = int(time.time())
            # Requête des clés configurées avec AutomaticRotationStrategy ou un intervalle de rotation
            # Pour l'exercice, on interroge les clés dont le metadata_json contient un intervalle ou une règle de rotation
            keys = self.db.keys.list_all()
            rotated_count = 0
            failures = 0

            for k in keys:
                meta = {}
                try:
                    meta = json.loads(k.get("metadata_json") or "{}")
                except Exception:
                    pass

                rotation_interval_days = meta.get("rotation_interval_days")
                if not rotation_interval_days:
                    continue  # Pas de stratégie automatique configurée

                last_rotated = meta.get("last_rotated_at", k.get("created_at", now))
                interval_seconds = rotation_interval_days * 86400

                if (now - last_rotated) > interval_seconds:
                    logger.info(f"Triggering automatic rotation for key {k['ref']} (interval: {rotation_interval_days} days)")
                    res = self.rotate_key(k["ref"])
                    if res.get("status") == "ok":
                        rotated_count += 1
                        # Mettre à jour last_rotated_at sur la nouvelle clé ou l'existante
                        meta["last_rotated_at"] = now
                        self.db.keys.update(k["ref"], metadata_json=json.dumps(meta))
                    else:
                        failures += 1

            return {"status": "ok", "rotated_count": rotated_count, "failures": failures}
        except Exception as e:
            logger.error(f"Error in check_and_rotate_expired: {str(e)}")
            return {"status": "error", "message": str(e)}

    def revoke_expired_grace_periods(self):
        """Révoque les anciennes clés dont la période de grâce est expirée."""
        try:
            now = int(time.time())
            keys = self.db.keys.list_all()
            revoked_count = 0

            for k in keys:
                meta = {}
                try:
                    meta = json.loads(k.get("metadata_json") or "{}")
                except Exception:
                    pass

                grace_until = meta.get("grace_until")
                if grace_until and now > grace_until and k.get("health_status") != "user_disabled":
                    # Marquer comme révoqué / désactivé dans la base et supprimer du cache
                    ref = k["ref"]
                    self.db.keys.update_health(ref, "user_disabled", error="Grace period expired, key revoked.")
                    from modules.key_manager.key_manager import KeyManager
                    km = KeyManager(db=self.db)
                    km.delete_key(ref)
                    revoked_count += 1
                    logger.info(f"Revoked old key {ref} after grace period expiration.")

            return {"status": "ok", "revoked_count": revoked_count}
        except Exception as e:
            logger.error(f"Error revoking grace periods: {str(e)}")
            return {"status": "error", "message": str(e)}

    def _notify_services(self, provider_ref: str, old_ref: str, new_ref: str):
        """Notifie les services dépendants via webhook ou pub/sub (AgentQueue / WebSocket)."""
        try:
            # Enregistrement dans l'agent_queue ou log d'événement de notification
            payload = json.dumps({
                "event": "key_rotated",
                "provider_ref": provider_ref,
                "old_key_ref": old_ref,
                "new_key_ref": new_ref,
                "timestamp": int(time.time())
            })
            # Broadcast dans la queue d'événements
            self.db.conn.execute(
                "INSERT INTO agent_queue (from_agent_id, topic, message_type, content, status) VALUES (1, ?, 'notification', ?, 'TODO')",
                ("key_rotation_events", payload)
            )
            self.db.commit()
            logger.info(f"Dispatched rotation notification for provider {provider_ref}")
        except Exception as e:
            logger.error(f"Failed to dispatch notification: {str(e)}")

