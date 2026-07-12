from pathlib import Path
from typing import List, Dict, Any, Optional

from modules.sql.db import ModelWeaverDB, CatalogueDB
from .fetcher import Fetcher


class Catalogue:
    """Catalogue des fournisseurs, modèles et outils.

    Utilise modules.sql.db en interne — aucun SQL ni JSON visible ici.
    """

    def __init__(self, db: Optional[ModelWeaverDB] = None):
        self.db = db or ModelWeaverDB()
        self.fetcher = Fetcher()
        self._cache = CacheStore()

    def list_providers(self, provider_type: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.providers.list_all(provider_type=provider_type)

    def get_provider(self, ref: str) -> Optional[Dict[str, Any]]:
        return self.db.providers.get(ref)

    def add_provider(self, data: Dict[str, Any]) -> int:
        return self.db.providers.save(data)

    def list_models(self, developer: Optional[str] = None,
                    modality: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.db.models.list_all(developer=developer, modality=modality)

    def get_model(self, ref: str) -> Optional[Dict[str, Any]]:
        return self.db.models.get(ref)

    def add_model(self, data: Dict[str, Any]) -> int:
        return self.db.models.save(data)

    def search_models(self, query: str) -> List[Dict[str, Any]]:
        return self.db.models.search(query)

    def list_models_by_provider(self, provider_ref: str) -> List[Dict[str, Any]]:
        return self.db.providers.get_provider_models(provider_ref)

    def get_models_by_provider(self, provider_id: str) -> List[Dict[str, Any]]:
        return self.db.providers.get_provider_models(provider_id)

    def add_tool(self, data: Dict[str, Any]) -> int:
        return self.db.tools.save(data)

    def sync_with_remote(self) -> None:
        """Synchronise avec models.dev + NVIDIA → écrit dans la BDD."""
        print("🔄 Synchronisation du catalogue...")

        api_data = self.fetcher.fetch_models_dev()
        if api_data:
            synced = 0
            for provider_id, provider_info in api_data.items():
                pid = self.db.providers.save({
                    "ref": provider_id,
                    "name": provider_id.capitalize(),
                    "provider_type": "cloud",
                    "catalogue_ref": provider_id,
                })
                models_dict = provider_info.get("models", {})
                for model_id, model_info in models_dict.items():
                    if not self.fetcher.is_chat_model(model_id):
                        continue
                    base_ref = model_id.split("/")[-1] if "/" in model_id else model_id
                    mid = self.db.models.save({
                        "ref": base_ref, "name": base_ref,
                        "developer": model_id.split("/")[0] if "/" in model_id else None,
                    })
                    self.db.models.link_provider(pid, mid, model_id, {
                        "context_window_tokens": model_info.get("max_input_tokens"),
                        "metadata_json": str({"is_chat_model": True}),
                    })
                    synced += 1
            print(f"✅ Synchronisé depuis models.dev : {synced} modèles")
            self.db.commit()

        nvidia_models = self.fetcher.fetch_nvidia_models()
        if nvidia_models:
            nid = self.db.providers.save({
                "ref": "nvidia", "name": "NVIDIA",
                "provider_type": "cloud", "catalogue_ref": "nvidia",
            })
            for nm in nvidia_models:
                name = nm.get("id", "")
                if not name:
                    continue
                mid = self.db.models.save({"ref": name, "name": name})
                self.db.models.link_provider(nid, mid, name, {
                    "context_window_tokens": nm.get("context_window"),
                })
            print(f"✅ Ajoutés depuis NVIDIA : {len(nvidia_models)} modèles")
            self.db.commit()


class CacheStore:
    """Cache mémoire simple (remplace les anciens _providers, _models lists)."""
    pass
