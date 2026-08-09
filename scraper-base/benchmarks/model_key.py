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
# Les suffixes de VARIANTE QUALITÉ (high/low/medium/thinking/reasoning…) sont
# des déclinaisons d'un même modèle (ex. gpt-5-high = gpt-5, grok-3-mini-high =
# grok-3-mini) → retirés aussi, sinon le matching échoue sur ces variantes.
_SUFFIX_PATTERNS = [
    r"(?:-|:)(?:latest|free|batch|preview|exp|test|discounted|base)",
    r"(?:-|:)(?:high|low|medium|xhigh|minimal|adaptive)$",
    r"(?:-|:)(?:thinking|reasoning|non-reasoning|nonreasoning)$",
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


def _unspace_versions(name: str) -> str:
    """Convertit les versions AA en tirets vers le format points du catalogue.

    Règles PRUDENTES (ne cassent pas les refs existantes) :
      1. « N-M » avec N chiffre SIMPLE (1-9) → « N.M » :
         gemini-3-5-flash → gemini-3.5-flash, claude-3-5-sonnet → claude-3.5-sonnet,
         qwen3-5-27b → qwen3.5-27b. Exclut les dates (2025-11) et tailles (a35b).
      2. « NM » concaténé (claude-35-sonnet) → « N.M » pour les familles connues.
    """
    # Règle 1 : N-M séparé par tiret (N simple, M quelconque) → N.M
    # Pattern : bordure non-chiffre puis D1-D2... On exige que le 1er chiffre
    # soit simple (1-9) et qu'il ne soit PAS précédé d'un autre chiffre.
    out = []
    i = 0
    n = len(name)
    while i < n:
        # Chercher une séquence \d-\d{1,2} (version mineure 0-99) :
        # gemini-3-5 → 3.5, gemini-2-0 → 2.0, claude-3-5 → 3.5.
        # Exclusions (tailles / dates, PAS des versions) :
        #   - d2 à 2 chiffres ≥ 10 (qwen2-72b → 72 = taille, pas 2.72)
        #   - d2 suivi d'un 'b' (qwen3-35b → 35b = taille)
        #   - nombre à plusieurs chiffres avant le tiret (2025-11 = date)
        m = re.search(r"(\d)-(\d{1,2})(?![0-9])", name[i:])
        if not m:
            out.append(name[i:])
            break
        start = i + m.start()
        end = i + m.end()
        d1, d2 = m.group(1), m.group(2)
        prev_ok = (start == 0) or (not name[start - 1].isdigit())
        # Taille (suivi de 'b') ou mineure ≥ 10 (72b, 397b…) → PAS une version.
        next_ch = name[end] if end < n else ""
        is_size = (next_ch == "b") or (int(d2) >= 10)
        if prev_ok and d1 in "123456789" and not is_size:
            out.append(name[i:start])
            out.append(f"{d1}.{d2}")
        else:
            out.append(name[i:start])
            out.append(f"{d1}-{d2}")
        i = end
    name = "".join(out)
    # Règle 2 : NM concaténé pour familles connues (claude-35-sonnet → 3.5)
    m = re.match(r"^(claude|gemini|gpt|qwen|glm|grok|llama|mistral|o[0-9])"
                 r"-(\d)(\d)(-.*)?$", name)
    if m and m.group(2) in "123456789":
        name = f"{m.group(1)}-{m.group(2)}.{m.group(3)}{m.group(4) or ''}"
    return name


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
    # Artificial Analysis nomme les versions avec des TIRETS (gemini-3-5-flash,
    # claude-35-sonnet, qwen3-5-27b) alors que le catalogue utilise souvent des
    # POINTS (gemini-3.5-flash, claude-3.5-sonnet, qwen3.5-27b).
    #
    # Règles de conversion (PRUDENTES, pour ne pas casser les refs existantes) :
    #   1. « N-M » après un nom (N,M chiffres) → « N.M » UNIQUEMENT si N est un
    #      chiffre SIMPLE (1-9) : gemini-3-5 → gemini-3.5, claude-3-5 → claude-3.5,
    #      qwen3-5 → qwen3.5. Exclut les dates (2025-11-13) et les tailles
    #      (a35b-...), qui commencent par plusieurs chiffres.
    #   2. « NM » concaténé après un nom (claude-35-sonnet) → « N.M »
    #      UNIQUEMENT si le préfixe est une famille connue (claude, gemini,
    #      gpt, qwen, glm, grok, llama, mistral…).
    name = _unspace_versions(name)
    # Normalisation finale des aliases/renommages.
    name = _ALIASES.get(name, name)
    return name


def models_equal(ref_a: str, ref_b: str) -> bool:
    """Vrai si deux refs désignent le même modèle (même model_key)."""
    return bool(ref_a) and bool(ref_b) and model_key(ref_a) == model_key(ref_b)
