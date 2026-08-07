"""model_key — normalisation des références de modèles en un identifiant canonique.

Le même modèle physique existe sous plusieurs refs dans le catalogue
(ex. `deepseek-v4-flash`, `deepseek-ai/deepseek-v4-flash`,
`kilo/deepseek/deepseek-v4-flash`, `huggingface/deepseek-ai/DeepSeek-V4-Flash`,
`openrouter/deepseek/deepseek-v4-flash-0731`, `opencode-zen/deepseek-v4-flash-free`).

`model_key()` réduit toutes ces variantes à UN identifiant canonique (ex.
`deepseek-v4-flash`) pour :
  - agréger les scores de benchmark par MODÈLE (pas par provider_model),
  - comparer les modèles entre eux dans l'allocation,
  - matcher les noms des sources de benchmark (artificialanalysis, lmsys, …)
    avec tolérance casse / renommage / suffixe.

Règles de normalisation :
  1. retirer les préfixes de provider/catégorie connus (google/, openrouter/,
     kilo/, nvidia/, opencode-zen/, huggingface/, deepseek/, openai/, groq/…),
  2. tout en minuscules, espaces/underscores → `-`,
  3. retirer les suffixes de variante (:free, :batch, :0731, -latest, -preview,
     -exp, -latest, :discounted, :16b, :preview, -0708…),
  4. normaliser quelques renommages courants entre sources.
"""

from __future__ import annotations

import re
from typing import Optional

# Préfixes provider/catégorie à retirer (dans l'ordre : le plus long d'abord).
_PROVIDER_PREFIXES = [
    "opencode-zen/", "openrouter/", "huggingface/", "ollama-cloud/",
    "ollama/", "nvidia/", "kilo/", "llm7/", "google/", "openai/", "groq/",
    "cohere/", "mistral/", "deepseek/", "together/", "deepinfra/",
    "openai", "gemini/", "meta-llama/", "qwen/", "anthropic/", "mistralai/",
    "meta/", "microsoft/", "nvidia/nvidia/", "kilo/kilo/", "deepseek/deepseek/",
    "google/google/", "openrouter/openrouter/", "opencode-zen/opencode-zen/",
    "~", "openai/", "deepseek-ai/",
]

# Renommages courants inter-sources → canonique.
_ALIASES = {
    "deepseek-chat": "deepseek-v3",
    "deepseek-v3-0324": "deepseek-v3",
    "deepseek-chat-v3-0324": "deepseek-v3",
    "deepseek-chat-v3.1": "deepseek-v3.1",
    "deepseek-v3.1-terminus": "deepseek-v3.1",
    "llama-3.3-70b-versatile": "llama-3.3-70b",
    "llama-3.3-70b-instruct": "llama-3.3-70b",
    "llama-3.2-11b-vision-instruct": "llama-3.2-11b-vision",
    "command-r7b-12-2024": "command-r7b",
    "gpt-5.1-codex-max": "gpt-5.1-codex-max",
    "gpt-5-codex": "gpt-5.1-codex-max",
    "gemini-3.5-flash-lite": "gemini-3.5-flash-lite",
    "gemini-3.5-flash": "gemini-3.5-flash",
    "gemini-3.6-flash": "gemini-3.6-flash",
    "poolside-laguna-s-2.1": "poolside-laguna-s-2.1",
}

# Suffixes de VARIANTE TECHNIQUE à retirer (version/date/batch/quant/preview) —
# PAS les mots flash/pro/ultra/nano qui font partie du NOM du modèle
# (ex. deepseek-v4-flash : « flash » est le nom, pas une variante).
_SUFFIX_PATTERNS = [
    r"(?:-|:)(?:latest|free|batch|preview|exp|test|discounted|base)",
    r":\d{4,}",           # :0731, :16b, :0708
    r"-\d{4}",            # -0528, -0324
    r":\d+(?:\.\d+)?b",   # :16b, :1.5b
]


def _strip_provider(ref: str) -> str:
    """Retire le préfixe provider/catégorie éventuel."""
    for p in sorted(_PROVIDER_PREFIXES, key=len, reverse=True):
        if ref.startswith(p):
            ref = ref[len(p):]
            break
    # Cas « provider/double-provider/model » restant (openrouter/~/deepseek/…)
    parts = ref.split("/")
    # Garder uniquement le dernier segment si plusieurs restent après nettoyage
    # de préfixes connus (ex. `deepseek/deepseek-v4-flash` → `deepseek-v4-flash`).
    if len(parts) > 1:
        ref = parts[-1]
    return ref


def _strip_suffixes(name: str) -> str:
    """Retire les suffixes de variante (version/batch/quant/preview…)."""
    # Retirer d'abord les variantes à deux segments type `-latest`, `:free`.
    for pat in _SUFFIX_PATTERNS:
        name = re.sub(pat + r"$", "", name)
    # Aplatir les tirets/underscores répétés et les « : » restants
    # (les points sont conservés : gemini-3.5-flash-lite garde son point).
    name = re.sub(r"[:_]+", "-", name)
    name = re.sub(r"-{2,}", "-", name)
    return name.strip("-")


def model_key(ref: str) -> str:
    """Normalise une ref de modèle en identifiant canonique par MODÈLE.

    Exemples :
      deepseek-ai/deepseek-v4-flash       → deepseek-v4-flash
      kilo/deepseek/deepseek-v4-flash     → deepseek-v4-flash
      huggingface/deepseek-ai/DeepSeek-V4-Flash → deepseek-v4-flash
      opencode-zen/deepseek-v4-flash-free → deepseek-v4-flash
      openrouter/deepseek/deepseek-v4-flash-0731 → deepseek-v4-flash
      google/gemini-3.5-flash-lite        → gemini-3.5-flash-lite
      openrouter/poolside/laguna-s-2.1:free → poolside-laguna-s-2.1
    """
    if not ref:
        return ""
    name = _strip_provider(ref).strip().lower()
    name = re.sub(r"[\s_]+", "-", name)
    name = _strip_suffixes(name)
    # Normalisation finale des aliases/renommages.
    name = _ALIASES.get(name, name)
    return name


def models_equal(ref_a: str, ref_b: str) -> bool:
    """Vrai si deux refs désignent le même modèle (même model_key)."""
    return bool(ref_a) and bool(ref_b) and model_key(ref_a) == model_key(ref_b)
