#!/usr/bin/env python3
"""Prépare les clés API pour litellm à partir du .env.

Lit le .env du projet, extrait les clés et génère
.modelweaver/keys.json avec le mapping provider → clé.

Les noms de variables .env sont mappés aux providers litellm :
  OPENAI_API_KEY        → openai
  GOOGLE_GEMINI_API_KEY → gemini
  MISTRAL_API_KEY       → mistral
  GROQ_API_KEY          → groq
  DEEPSEEK_API_KEY      → deepseek
  COHERE_API_KEY        → cohere
  HUGGINGFACE_API_KEY   → huggingface
  OPENROUTER_API_KEY    → openrouter
  OPENCODE_ZEN_API_KEY  → opencode-zen (avec api_base personnalisé)
"""

import json, re, os, sys
from pathlib import Path

# Mapper variable .env → provider litellm
# (nom_var, provider_id, {extra_params optionnels})
ENV_MAP = [
    ("OPENAI_API_KEY",        "openai",       {}),
    ("GOOGLE_GEMINI_API_KEY", "gemini",       {}),
    ("MISTRAL_API_KEY",       "mistral",      {}),
    ("GROQ_API_KEY",          "groq",         {}),
    ("DEEPSEEK_API_KEY",      "deepseek",     {}),
    ("COHERE_API_KEY",        "cohere",       {}),
    ("HUGGINGFACE_API_KEY",   "huggingface",  {}),
    ("OPENROUTER_API_KEY",    "openrouter",   {}),
    # Provider spéciaux (api_base personnalisé)
    ("OPENCODE_ZEN_API_KEY",  "opencode-zen", {"api_base": "https://opencode.ai/zen/v1"}),
]

APP_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_PATH = APP_DIR.parent / ".env"
OUTPUT_PATH = APP_DIR / ".modelweaver" / "keys.json"


def load_env(path: Path) -> dict:
    """Lit un fichier .env, retourne {VAR: valeur}."""
    if not path.exists():
        print(f"⚠️  {path} introuvable")
        return {}
    keys = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^export\s+", line)
            if m:
                line = line[m.end():]
            if "=" not in line:
                continue
            var, val = line.split("=", 1)
            var = var.strip()
            val = val.strip().strip("\"'")
            if val and val != "''" and val != '""':
                keys[var] = val
    return keys


def run(env_path: str | None = None):
    path = Path(env_path) if env_path else DEFAULT_ENV_PATH
    env_keys = load_env(path)

    if not env_keys:
        print(f"❌ Aucune clé trouvée dans {path}")
        print(f"   Crée un fichier .env à la racine du projet avec les clés API")
        sys.exit(1)

    result = {}
    for var, provider, extra in ENV_MAP:
        if var not in env_keys:
            continue
        entry = {"api_key": env_keys[var]}
        if extra:
            entry.update(extra)
        result[provider] = entry

        # Redacted display
        key_preview = env_keys[var][:8] + "..." if len(env_keys[var]) > 12 else env_keys[var]
        print(f"  ✓ {provider:15s} ← {var} ({key_preview})")

    if not result:
        print("❌ Aucune clé reconnue dans .env")
        print(f"   Variables trouvées : {list(env_keys.keys())}")
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n✅ {len(result)} provider(s) enregistré(s) dans {OUTPUT_PATH}")


if __name__ == "__main__":
    env_path = sys.argv[1] if len(sys.argv) > 1 else None
    run(env_path)
