"""Consumer IN : onboarding des clés API depuis .env vers keys.db.

Lit les variables d'environnement .env (mapping provider→env vars) et
enregistre chaque clé via KeyManager (secrets → keyring OS / Fernet,
métadonnées → keys.db). Idempotent : une clé déjà présente (même provider+identity)
n'est pas dupliquée.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

_ENV_KEY_MAP: Dict[str, List[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "nvidia": ["NVIDIA_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "cohere": ["COHERE_API_KEY"],
    "huggingface": ["HUGGINGFACE_API_KEY", "HF_API_KEY"],
    "artificial_analysis": ["AA_API_KEY"],
}


def _load_env_file(env_path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("\"'")
    return env


def onboard_from_env(env_path: Path | None = None) -> dict:
    from modules.sqlite.keys.key_manager import KeyManager
    from modules.sqlite.paths import mw_home
    if env_path is None:
        env_path = Path.cwd() / ".env"
        if not env_path.exists():
            env_path = Path.home() / ".env"
    env = _load_env_file(env_path)
    if not env:
        return {"ok": True, "loaded": 0, "note": "no .env found"}
    km = KeyManager()
    existing = {r["provider_ref"] for r in km.list_keys()}
    loaded = []
    for ref, vars_ in _ENV_KEY_MAP.items():
        for var in vars_:
            val = env.get(var) or __import__("os").environ.get(var)
            if not val:
                continue
            try:
                km.set_key(ref, val, tag="default")
                loaded.append(ref)
            except Exception as e:
                loaded.append({"ref": ref, "error": str(e)})
            break
    return {"ok": True, "loaded": loaded, "existing": sorted(existing)}
