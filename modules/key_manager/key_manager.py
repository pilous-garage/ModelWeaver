import json
from pathlib import Path
from typing import Dict, Any, Optional

class KeyManager:
    def __init__(self, vault_path: Optional[Path] = None):
        if vault_path:
            self.vault_path = Path(vault_path)
        else:
            # Default to a hidden directory in home or project
            self.vault_path = Path(__file__).parent.parent / ".modelweaver" / "vault.json"
        
        self._keys: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        """Loads the keys from the vault."""
        if self.vault_path.exists():
            try:
                with open(self.vault_path, "r", encoding="utf-8") as f:
                    self._keys = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Error loading vault: {e}")
                self._keys = {}
        else:
            self._keys = {}

    def save(self) -> None:
        """Saves the keys to the vault."""
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.vault_path, "w", encoding="utf-8") as f:
                json.dump(self._keys, f, indent=2, ensure_ascii=False)
        except IOError as e:
            print(f"Error saving vault: {e}")

    def set_key(self, provider_id: str, api_key: str, api_base: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Sets a key for a provider."""
        self._keys[provider_id] = {
            "api_key": api_key,
            "api_base": api_base,
            "metadata": metadata or {}
        }
        self.save()

    def get_key(self, provider_id: str) -> Optional[Dict[str, Any]]:
        """Gets the key for a provider."""
        return self._keys.get(provider_id)

    def delete_key(self, provider_id: str) -> None:
        """Deletes a key for a provider."""
        if provider_id in self._keys:
            del self._keys[provider_id]
            self.save()

    def list_providers(self) -> list[str]:
        """Lists all providers that have a key."""
        return list(self._keys.keys())

if __name__ == "__main__":
    # Quick test
    km = KeyManager(vault_path=Path("test_vault.json"))
    km.set_key("openai", "sk-123", api_base="https://api.openai.com/v1")
    print(f"Keys: {km.list_providers()}")
    print(f"OpenAI key: {km.get_key('openai')}")
    km.delete_key("openai")
    print(f"Keys after delete: {km.list_providers()}")
    import os
    if os.path.exists("test_vault.json"):
        os.remove("test_vault.json")
