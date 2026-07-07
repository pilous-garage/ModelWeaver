import os
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
from .key_manager import KeyManager

class Onboarder:
    def __init__(self, key_manager: KeyManager, catalogue: Optional[Any] = None):
        self.key_manager = key_manager
        self.catalogue = catalogue
        # Common patterns for API keys in .env files
        self.patterns = {
            "openai": re.compile(r"OPENAI_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?"),
            "anthropic": re.compile(r"ANTHROPIC_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?"),
            "google": re.compile(r"GOOGLE_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?"),
            "nvidia": re.compile(r"NVIDIA_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?"),
            "groq": re.compile(r"GROQ_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?"),
            "mistral": re.compile(r"MISTRAL_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?"),
            "cohere": re.compile(r"COHERE_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?"),
        }

    def onboard_from_env(self, env_path: Path) -> int:
        """Scans an .env file and adds discovered keys to the KeyManager."""
        if not env_path.exists():
            return 0
        
        found_count = 0
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()
                for provider, pattern in self.patterns.items():
                    match = pattern.search(content)
                    if match:
                        api_key = match.group(1).strip()
                        if api_key and not self.key_manager.get_key(provider):
                            self.key_manager.set_key(provider, api_key)
                            print(f"✅ Onboarded {provider} key from {env_path}")
                            found_count += 1
            
            if found_count > 0 and self.catalogue:
                self._enrich_onboarded_keys()
                
        except Exception as e:
            print(f"Error during onboarding from {env_path}: {e}")
            
        return found_count

    def _enrich_onboarded_keys(self) -> None:
        """Enriches onboarded keys with information from the catalogue."""
        print("🔍 Enriching onboarded keys with catalogue information...")
        for provider_id in self.key_manager.list_providers():
            key_info = self.key_manager.get_key(provider_id)
            if key_info:
                # Try to find models in catalogue for this provider
                models = self.catalogue.get_models_by_provider(provider_id)
                if models:
                    key_info["metadata"]["available_models"] = [m["id"] for m in models]
                    print(f"   ↳ {provider_id}: enriched with {len(models)} models")
                else:
                    print(f"   ↳ {provider_id}: no models found in catalogue")

if __name__ == "__main__":
    # Quick test
    from pathlib import Path
    test_env = Path("test.env")
    test_env.write_text("OPENAI_API_KEY=sk-test-123\nGROQ_API_KEY=gsk-test-456")
    
    km = KeyManager(vault_path=Path("test_vault.json"))
    # Mock catalogue
    class MockCatalogue:
        def get_models_by_provider(self, provider_id):
            if provider_id == "openai":
                return [{"id": "gpt-4"}]
            return []
            
    onboarder = Onboarder(km, catalogue=MockCatalogue())
    count = onboarder.onboard_from_env(test_env)
    print(f"Found {count} keys")
    print(f"Keys: {km.list_providers()}")
    print(f"OpenAI key info: {km.get_key('openai')}")
    
    import os
    if test_env.exists(): os.remove(test_env)
    if Path("test_vault.json").exists(): os.remove("test_vault.json")

