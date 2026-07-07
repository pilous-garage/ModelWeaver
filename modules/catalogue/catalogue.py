import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from .fetcher import Fetcher

import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from .fetcher import Fetcher

class Catalogue:
    def __init__(self, data_dir: Optional[Path] = None, cache_dir: Optional[Path] = None):
        if data_dir:
            self.data_dir = Path(data_dir)
        else:
            self.data_dir = Path(__file__).parent / "data"
        
        self.cache_dir = cache_dir or Path(__file__).parent.parent / ".modelweaver" / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.providers_file = self.data_dir / "providers.json"
        self.models_file = self.data_dir / "models.json"
        self.tools_file = self.data_dir / "tools.json"
        
        self._providers: List[Dict[str, Any]] = []
        self._models: List[Dict[str, Any]] = []
        self._tools: List[Dict[str, Any]] = []
        
        self.fetcher = Fetcher()
        self.load()

    def load(self) -> None:
        """Loads the catalogue data from JSON files."""
        self._providers = self._load_file(self.providers_file)
        self._models = self._load_file(self.models_file)
        self._tools = self._load_file(self.tools_file)

    def sync_with_remote(self) -> None:
        """Synchronizes the catalogue with remote sources (models.dev and NVIDIA)."""
        print("🔄 Synchronizing catalogue with remote sources...")
        
        # 1. Fetch from models.dev
        api_data = self.fetcher.fetch_models_dev()
        if not api_data:
            print("⚠️  Failed to fetch from models.dev, skipping.")
        else:
            # Update providers and models from models.dev
            new_models = []
            for provider_id, provider_info in api_data.items():
                # Add provider if not exists
                if not self.get_provider(provider_id):
                    self.add_provider({
                        "id": provider_id,
                        "name": provider_id.capitalize(),
                        "type": "cloud"
                    })
                
                models_dict = provider_info.get("models", {})
                for model_id, model_info in models_dict.items():
                    if self.fetcher.is_chat_model(model_id):
                        new_models.append({
                            "id": f"{provider_id}-{model_id.replace('/', '-')}",
                            "provider_id": provider_id,
                            "name": model_id,
                            "is_chat_model": True
                        })
            
            # Merge new models with existing ones (simple replacement for now)
            self._models = new_models
            print(f"✅ Synchronized {len(self._models)} models from models.dev")

        # 2. Fetch from NVIDIA
        nvidia_models = self.fetcher.fetch_nvidia_models()
        if nvidia_models:
            for nm in nvidia_models:
                m_id = nm["id"]
                if not self.get_model(m_id):
                    self._models.append({
                        "id": m_id,
                        "provider_id": "nvidia",
                        "name": m_id,
                        "is_chat_model": True
                    })
            print(f"✅ Added {len(nvidia_models)} models from NVIDIA")
            
        self.save()

    def _load_file(self, file_path: Path) -> List[Dict[str, Any]]:
        """Helper to load a JSON list from a file."""
        if not file_path.exists():
            return []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                else:
                    print(f"Warning: Expected list in {file_path}, got {type(data)}")
                    return []
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading {file_path}: {e}")
            return []

    def save(self) -> None:
        """Saves the catalogue data to JSON files."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._save_file(self.providers_file, self._providers)
        self._save_file(self.models_file, self._models)
        self._save_file(self.tools_file, self._tools)

    def _save_file(self, file_path: Path, data: List[Dict[str, Any]]) -> None:
        """Helper to save a JSON list to a file."""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"Error saving {file_path}: {e}")

    # Providers
    def get_providers(self) -> List[Dict[str, Any]]:
        return self._providers

    def add_provider(self, provider: Dict[str, Any]) -> None:
        if any(p["id"] == provider["id"] for p in self._providers):
            raise ValueError(f"Provider with id {provider['id']} already exists")
        self._providers.append(provider)

    def get_provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self._providers if p["id"] == provider_id), None)

    # Models
    def get_models(self) -> List[Dict[str, Any]]:
        return self._models

    def get_models_by_provider(self, provider_id: str) -> List[Dict[str, Any]]:
        return [m for m in self._models if m.get("provider_id") == provider_id]

    def add_model(self, model: Dict[str, Any]) -> None:
        if any(m["id"] == model["id"] for m in self._models):
            raise ValueError(f"Model with id {model['id']} already exists")
        self._models.append(model)

    def get_model(self, model_id: str) -> Optional[Dict[str, Any]]:
        return next((m for m in self._models if m["id"] == model_id), None)

    def update_model(self, model_id: str, updates: Dict[str, Any]) -> None:
        for m in self._models:
            if m["id"] == model_id:
                m.update(updates)
                return
        raise ValueError(f"Model with id {model_id} not found")

    # Tools
    def get_tools(self) -> List[Dict[str, Any]]:
        return self._tools

    def add_tool(self, tool: Dict[str, Any]) -> None:
        if any(t["id"] == tool["id"] for t in self._tools):
            raise ValueError(f"Tool with id {tool['id']} already exists")
        self._tools.append(tool)

    def get_tool(self, tool_id: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self._tools if t["id"] == tool_id), None)

if __name__ == "__main__":
    # Quick test
    cat = Catalogue()
    cat.add_provider({"id": "test_prov", "name": "Test Provider", "type": "local"})
    cat.add_model({"id": "test_model", "provider_id": "test_prov", "name": "Test Model", "is_chat_model": True})
    cat.save()
    print("Test complete. Check modules/catalogue/data/")

    def load(self) -> None:
        """Loads the catalogue data from JSON files."""
        self._providers = self._load_file(self.providers_file)
        self._models = self._load_file(self.models_file)
        self._tools = self._load_file(self.tools_file)

    def sync_with_remote(self) -> None:
        """Synchronizes the catalogue with remote sources (models.dev and NVIDIA)."""
        print("🔄 Synchronizing catalogue with remote sources...")
        
        # 1. Fetch from models.dev
        api_data = self.fetcher.fetch_models_dev()
        if not api_data:
            print("⚠️  Failed to fetch from models.dev, skipping.")
        else:
            # Update providers and models from models.dev
            new_models = []
            for provider_id, provider_info in api_data.items():
                # Add provider if not exists
                if not self.get_provider(provider_id):
                    self.add_provider({
                        "id": provider_id,
                        "name": provider_id.capitalize(),
                        "type": "cloud"
                    })
                
                models_dict = provider_info.get("models", {})
                for model_id, model_info in models_dict.items():
                    if self.fetcher.is_chat_model(model_id):
                        new_models.append({
                            "id": f"{provider_id}-{model_id.replace('/', '-')}",
                            "provider_id": provider_id,
                            "name": model_id,
                            "is_chat_model": True
                        })
            
            # Merge new models with existing ones (simple replacement for now)
            self._models = new_models
            print(f"✅ Synchronized {len(self._models)} models from models.dev")

        # 2. Fetch from NVIDIA
        nvidia_models = self.fetcher.fetch_nvidia_models()
        if nvidia_models:
            for nm in nvidia_models:
                m_id = nm["id"]
                if not self.get_model(m_id):
                    self._models.append({
                        "id": m_id,
                        "provider_id": "nvidia",
                        "name": m_id,
                        "is_chat_model": True
                    })
            print(f"✅ Added {len(nvidia_models)} models from NVIDIA")
            
        self.save()

    def _load_file(self, file_path: Path) -> List[Dict[str, Any]]:
        """Helper to load a JSON list from a file."""
        if not file_path.exists():
            return []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                else:
                    print(f"Warning: Expected list in {file_path}, got {type(data)}")
                    return []
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading {file_path}: {e}")
            return []

    def save(self) -> None:
        """Saves the catalogue data to JSON files."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._save_file(self.providers_file, self._providers)
        self._save_file(self.models_file, self._models)
        self._save_file(self.tools_file, self._tools)

    def _save_file(self, file_path: Path, data: List[Dict[str, Any]]) -> None:
        """Helper to save a JSON list to a file."""
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"Error saving {file_path}: {e}")

    # Providers
    def get_providers(self) -> List[Dict[str, Any]]:
        return self._providers

    def add_provider(self, provider: Dict[str, Any]) -> None:
        if any(p["id"] == provider["id"] for p in self._providers):
            raise ValueError(f"Provider with id {provider['id']} already exists")
        self._providers.append(provider)

    def get_provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self._providers if p["id"] == provider_id), None)

    # Models
    def get_models(self) -> List[Dict[str, Any]]:
        return self._models

    def get_models_by_provider(self, provider_id: str) -> List[Dict[str, Any]]:
        return [m for m in self._models if m.get("provider_id") == provider_id]

    def add_model(self, model: Dict[str, Any]) -> None:
        if any(m["id"] == model["id"] for m in self._models):
            raise ValueError(f"Model with id {model['id']} already exists")
        self._models.append(model)

    def get_model(self, model_id: str) -> Optional[Dict[str, Any]]:
        return next((m for m in self._models if m["id"] == model_id), None)

    def update_model(self, model_id: str, updates: Dict[str, Any]) -> None:
        for m in self._models:
            if m["id"] == model_id:
                m.update(updates)
                return
        raise ValueError(f"Model with id {model_id} not found")

    # Tools
    def get_tools(self) -> List[Dict[str, Any]]:
        return self._tools

    def add_tool(self, tool: Dict[str, Any]) -> None:
        if any(t["id"] == tool["id"] for t in self._tools):
            raise ValueError(f"Tool with id {tool['id']} already exists")
        self._tools.append(tool)

    def get_tool(self, tool_id: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self._tools if t["id"] == tool_id), None)

if __name__ == "__main__":
    # Quick test
    cat = Catalogue()
    cat.add_provider({"id": "test_prov", "name": "Test Provider", "type": "local"})
    cat.add_model({"id": "test_model", "provider_id": "test_prov", "name": "Test Model", "is_chat_model": True})
    cat.save()
    print("Test complete. Check modules/catalogue/data/")
