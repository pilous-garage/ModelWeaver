#!/usr/bin/env python3
"""Met à jour la liste des modèles litellm depuis la source de vérité.

Pipeline :
  1. prepare_keys.py  (lit .env → .modelweaver/keys.json)
  2. Fetch models.dev API → liste complète des modèles
  3. Filtre par clés disponibles
  4. Ajoute les modèles spéciaux (Zen, etc.)
  5. Merge avec la config existante (marque "possibly-dead" sans supprimer)
  6. Sauvegarde litellm_config.yaml
  7. ordonner-fallback.py (applique les préférences de fallback)

Usage :
    python maj-liste-litellm.py              # mise à jour complète
    python maj-liste-litellm.py --dry-run    # simulation
    python maj-liste-litellm.py --skip-fetch # utilise le cache API
"""

import json, sys, os, subprocess, re, yaml
from pathlib import Path

# ─── Chemins ────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).resolve().parent
MODELWEAVER_DIR = APP_DIR / ".modelweaver"
KEYS_PATH = MODELWEAVER_DIR / "keys.json"
YAML_PATH = MODELWEAVER_DIR / "litellm_config.yaml"
API_CACHE = MODELWEAVER_DIR / "cache" / "models_api.json"
PREPARE_KEYS = APP_DIR / "prepare_keys.py"
ORDONNER_FALLBACK = APP_DIR / "ordonner-fallback.py"
PREFS_PATH = MODELWEAVER_DIR / "fallback_preferences.yaml"

API_URL = "https://models.dev/api.json"

# Budgets par défaut par provider (chars max de contexte)
DEFAULT_BUDGETS = {
    "gemini": 4_000_000,
    "openai": 400_000,
    "mistral": 400_000,
    "groq": 400_000,
    "deepseek": 400_000,
    "cohere": 400_000,
"huggingface": 400_000,
    "openrouter": 400_000,
    "nvidia": 400_000,
}

# Mapping provider models.dev → prefix litellm
PROVIDER_PREFIX_MAP = {
    "google": "gemini",
    "openai": "openai",
    "mistral": "mistral",
    "groq": "groq",
    "deepseek": "deepseek",
    "cohere": "cohere",
    "huggingface": "huggingface",
    "openrouter": "openrouter",
    "nvidia": "nvidia",
}

# Mots-clés pour filtrer les modèles non-chat
NON_CHAT_KEYWORDS = [
    "embedding", "embed", "tts", "speech", "whisper",
    "imagen", "veo", "lyria", "sora",
    "robotics", "learnlm",
    "prompt-guard", "safeguard",
]

# Modèles Zen à ajouter (provider "opencode-zen")
ZEN_MODELS = [
    {
        "model": "deepseek-v4-flash-free",
        "litellm_model": "openai/deepseek-v4-flash-free",
        "id": "zen-deepseek-v4-flash-free",
    },
]


# ─── Étape 1 : Clés ─────────────────────────────────────────────────────────
def run_prepare_keys():
    print("━" * 50)
    print("🔑 1/5 — Préparation des clés API")
    print("━" * 50)
    result = subprocess.run(
        [sys.executable, str(PREPARE_KEYS)],
        capture_output=False,
    )
    return result.returncode == 0


def load_keys() -> dict:
    with open(KEYS_PATH) as f:
        return json.load(f)


# ─── Étape 2 : Fetch API ────────────────────────────────────────────────────
def fetch_models_api(skip_fetch: bool = False) -> dict:
    print("\n" + "━" * 50)
    print("📡 2/5 — Récupération du registre models.dev")
    print("━" * 50)

    if skip_fetch and API_CACHE.exists():
        print(f"   ↳ Cache utilisé : {API_CACHE}")
        with open(API_CACHE) as f:
            return json.load(f)

    import urllib.request
    print(f"   ↳ Téléchargement depuis {API_URL}...")
    try:
        req = urllib.request.Request(API_URL, headers={"User-Agent": "ModelWeaver/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"   ❌ Échec : {e}")
        if API_CACHE.exists():
            print(f"   ↳ Fallback sur le cache : {API_CACHE}")
            with open(API_CACHE) as f:
                return json.load(f)
        sys.exit(1)

    API_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(API_CACHE, "w") as f:
        json.dump(data, f)
    print(f"   ✅ {len(data)} providers récupérés (cache: {API_CACHE})")
    return data


# ─── Étape 3 : Filtrage ─────────────────────────────────────────────────────
def is_chat_model(model_id: str) -> bool:
    mid = model_id.lower()
    for kw in NON_CHAT_KEYWORDS:
        if kw in mid:
            return False
    return True


def build_model_list(api_data: dict, keys: dict) -> list[dict]:
    print("\n" + "━" * 50)
    print("🔎 3/5 — Filtrage des modèles par clé API")
    print("━" * 50)

    model_list = []
    model_budgets = {}
    stats = {}

    for api_provider, prefix in PROVIDER_PREFIX_MAP.items():
        # Vérifier si on a la clé pour ce provider
        litellm_provider = prefix  # ex: "gemini", "openai", etc.
        if litellm_provider not in keys:
            # Chercher aussi dans les providers qui ont des noms différents
            # (ex: clé "gemini" pour l'API "google")
            matched = False
            for k in keys:
                if k == api_provider or api_provider.startswith(k):
                    matched = True
                    break
            if not matched:
                continue

        prov_data = api_data.get(api_provider)
        if not prov_data:
            continue

        models = prov_data.get("models", {})
        if not isinstance(models, dict):
            continue

        api_key = keys[litellm_provider]["api_key"]
        api_base = keys[litellm_provider].get("api_base")  # None pour la plupart
        budget = DEFAULT_BUDGETS.get(litellm_provider, 400_000)

        count = 0
        for model_id, model_info in models.items():
            if not is_chat_model(model_id):
                continue

            # Construire le nom litellm
            litellm_model = f"{prefix}/{model_id}"
            mid = f"{api_provider}-{model_id.replace('/', '-')}"

            entry = {
                "model_name": "opencode-engine",
                "litellm_params": {
                    "model": litellm_model,
                    "api_key": api_key,
                },
                "model_info": {
                    "id": mid,
                },
            }
            if api_base:
                entry["litellm_params"]["api_base"] = api_base

            model_list.append(entry)
            model_budgets[mid] = budget
            count += 1

        stats[api_provider] = count
        print(f"   ✓ {api_provider:15s} → {count} modèles")

    return model_list, model_budgets, stats


# ─── Étape 4 : Modèles spéciaux (Zen, etc.) ─────────────────────────────────
def add_special_models(model_list: list, model_budgets: dict, keys: dict):
    print("\n" + "━" * 50)
    print("🧩 4/5 — Ajout des modèles spéciaux")
    print("━" * 50)

    zen_key = keys.get("opencode-zen")
    if zen_key:
        for zm in ZEN_MODELS:
            entry = {
                "model_name": "opencode-engine",
                "litellm_params": {
                    "model": zm["litellm_model"],
                    "api_key": zen_key["api_key"],
                    "api_base": zen_key.get("api_base", "https://opencode.ai/zen/v1"),
                },
                "model_info": {
                    "id": zm["id"],
                },
            }
            model_list.append(entry)
            model_budgets[zm["id"]] = 400_000
            print(f"   ✓ {zm['id']}")
    else:
        print("   ⚠️  Pas de clé Zen — modèles Zen ignorés")


# ─── Étape 5 : Merge avec config existante ──────────────────────────────────
def load_current_config() -> tuple[list, dict]:
    if not YAML_PATH.exists():
        return [], {}
    with open(YAML_PATH) as f:
        cfg = yaml.safe_load(f)
    return cfg.get("model_list", []), cfg.get("model_budgets", {})


def merge_models(new_list: list, new_budgets: dict, old_list: list, old_budgets: dict) -> tuple[list, dict]:
    """Merge : préserve les modèles existants non trouvés en les marquant 'possibly-dead'."""
    print("\n" + "━" * 50)
    print("🔄 5/5 — Merge avec la config existante")
    print("━" * 50)

    # Construire un index des nouveaux modèles par (model, api_base)
    new_index = set()
    for m in new_list:
        model = m["litellm_params"]["model"]
        api_base = m["litellm_params"].get("api_base", "")
        new_index.add((model, api_base))

    preserved = 0
    marked_dead = 0

    for old_m in old_list:
        model = old_m["litellm_params"]["model"]
        api_base = old_m["litellm_params"].get("api_base", "")
        key = (model, api_base)

        if key not in new_index:
            # Modèle existant mais plus dans la source → marquer
            old_m.setdefault("model_info", {})["status"] = "possibly-dead"
            new_list.append(old_m)
            mid = old_m["model_info"].get("id", "")
            if mid and mid in old_budgets:
                new_budgets[mid] = old_budgets.get(mid, 400_000)
            marked_dead += 1
            label = mid or old_m["litellm_params"]["model"]
            print(f"   ⚰️  {label:40s} → possibly-dead")
        else:
            preserved += 1

    print(f"   ↳ {preserved} modèles préservés, {marked_dead} marqués possibly-dead")
    print(f"   ↳ Total après merge : {len(new_list)} modèles")
    return new_list, new_budgets


# ─── Étape 6 : Sauvegarde ───────────────────────────────────────────────────
def save_config(model_list: list, model_budgets: dict, old_config: dict):
    config = {
        "general_settings": old_config.get("general_settings", {
            "master_key": "sk-litellm-master",
        }),
        "router_settings": old_config.get("router_settings", {
            "routing_strategy": "latency-based-routing",
            "allowed_fails": 3,
            "num_retries": 1,
            "enable_pre_call_checks": True,
            "set_verbose": True,
        }),
        "context_settings": old_config.get("context_settings", {
            "enabled": True,
            "project_root": str(APP_DIR.parent),
            "include_tree": True,
            "include_summary": True,
            "max_context_chars": 100000,
            "refresh_interval": 60,
            "exclude_patterns": ["*.pyc", "__pycache__", "*.log", ".git", "venv", "node_modules", ".env*"],
            "max_file_size": 50000,
        }),
        "model_budgets": model_budgets,
        "model_list": model_list,
    }

    with open(YAML_PATH, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"\n💾 Config sauvegardée : {YAML_PATH}")
    print(f"   {len(model_list)} modèles, {len(model_budgets)} budgets")


# ─── Étape 7 : Ordonner fallback ────────────────────────────────────────────
def run_ordonner_fallback(dry_run: bool = False):
    print("\n" + "━" * 50)
    print("📋 Fallback : ordonnancement selon les préférences")
    print("━" * 50)
    cmd = [sys.executable, str(ORDONNER_FALLBACK)]
    if dry_run:
        cmd.append("--dry-run")
    if PREFS_PATH.exists():
        cmd += ["--prefs", str(PREFS_PATH)]
    result = subprocess.run(cmd)
    return result.returncode == 0


# ─── Main ────────────────────────────────────────────────────────────────────
def main():
    dry_run = "--dry-run" in sys.argv
    skip_fetch = "--skip-fetch" in sys.argv

    # 1. Clés
    if not run_prepare_keys():
        print("❌ prepare_keys.py a échoué")
        sys.exit(1)
    keys = load_keys()

    # 2. Fetch API
    api_data = fetch_models_api(skip_fetch)

    # 3. Build + filter
    new_list, new_budgets, stats = build_model_list(api_data, keys)

    # 4. Modèles spéciaux
    add_special_models(new_list, new_budgets, keys)

    # 5. Merge with existing
    old_list, old_budgets = load_current_config()
    merged_list, merged_budgets = merge_models(new_list, new_budgets, old_list, old_budgets)

    old_cfg = {}
    if YAML_PATH.exists():
        with open(YAML_PATH) as f:
            old_cfg = yaml.safe_load(f) or {}

    if dry_run:
        print("\n⚠️  DRY RUN — aucune écriture")
        print(f"   Serait sauvegardé : {len(merged_list)} modèles")
        print(f"   Puis ordonnancement par fallback preferences")
    else:
        # 6. Save
        save_config(merged_list, merged_budgets, old_cfg)

        # 7. Fallback order
        run_ordonner_fallback()

    print("\n" + "=" * 50)
    print(f"{'⚠️  DRY RUN' if dry_run else '✅'} Mise à jour terminée")
    print("=" * 50)


if __name__ == "__main__":
    main()
