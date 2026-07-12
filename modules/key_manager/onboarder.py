import re
from pathlib import Path
from typing import Optional

from .key_manager import KeyManager


class Onboarder:
    """Détecte et importe automatiquement les clés depuis .env."""

    def __init__(self, key_manager: KeyManager):
        self.key_manager = key_manager
        self.patterns = [
            (r"OPENAI_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "openai", "paid", "pro"),
            (r"ANTHROPIC_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "anthropic", "paid", "pro"),
            (r"GOOGLE_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "google", "paid", None),
            (r"GOOGLE_GEMINI_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "gemini", "free", None),
            (r"NVIDIA_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "nvidia", "free", None),
            (r"GROQ_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "groq", "free", None),
            (r"MISTRAL_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "mistral", "paid", None),
            (r"COHERE_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "cohere", "free", None),
            (r"DEEPSEEK_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "deepseek", "paid", None),
            (r"OPENROUTER_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "openrouter", "paid", None),
            (r"OPENCODE_ZEN_API_KEY\s*=\s*['\"]?([^'\"\s]+)['\"]?", "opencode-zen", "paid", None),
        ]

    def onboard_from_env(self, env_path: Path) -> int:
        if not env_path.exists():
            return 0

        found = 0
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                content = f.read()

            for pattern, provider, tag, grade in self.patterns:
                m = re.search(pattern, content)
                if not m:
                    continue
                api_key = m.group(1).strip()
                if not api_key:
                    continue
                existing = self.key_manager.get_key(provider)
                if existing:
                    continue
                api_base = None
                if provider == "opencode-zen":
                    api_base = "https://opencode.ai/zen/v1"
                self.key_manager.set_key(provider, api_key,
                                          api_base=api_base,
                                          tag=tag, grade=grade)
                preview = api_key[:8] + "..." if len(api_key) > 12 else api_key
                print(f"✅ Onboarded {provider:12s} ({tag}) ← {preview}")
                found += 1
        except Exception as e:
            print(f"⚠️  Error during onboarding: {e}")

        return found
