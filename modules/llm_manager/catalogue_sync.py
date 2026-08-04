"""Catalogue Sync — synchronisation des modèles et capacités depuis les providers.

Interroge chaque provider API pour :
  1. Lister les modèles disponibles
  2. Extraire leurs capacités (function calling, vision, chat, etc.)
  3. Mettre à jour le catalogue local (BDD, pas JSON)

Sources par ordre de priorité :
  a) API du provider (ex: OpenAI /v1/models, NVIDIA /v1/models)
  b) Base de connaissance locale (modèles connus par famille)
  c) Web scraping (HuggingFace, documentation provider)
  d) Test réel du modèle (dernier recours, coûteux)
"""

import json, logging, os, re, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

logger = logging.getLogger("modelweaver.catalogue_sync")


# ── Schéma de la table des capacités ──────────────────────────

CAPABILITIES_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_capabilities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    model_ref       TEXT NOT NULL UNIQUE,
    supports_chat           INTEGER DEFAULT 0,
    supports_function_calling INTEGER DEFAULT 0,
    supports_vision         INTEGER DEFAULT 0,
    supports_embedding      INTEGER DEFAULT 0,
    supports_streaming      INTEGER DEFAULT 0,
    supports_tools          INTEGER DEFAULT 0,  -- alias pour function_calling
    max_context_tokens      INTEGER,
    max_output_tokens       INTEGER,
    pricing_input_per_1k   REAL,
    pricing_output_per_1k  REAL,
    source                  TEXT DEFAULT 'unknown',  -- api | knowledge | scraping | test
    last_updated_at        INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(model_ref)
);
"""


# ── Base de connaissance par famille de modèles ───────────────

# Capacités connues par famille de modèles (source : documentation provider)
# Quand l'API ne fournit pas ces infos, on utilise cette table.
FAMILY_CAPABILITIES: Dict[str, Dict[str, Any]] = {
    # OpenAI
    "gpt-4o":         {"chat": True, "fc": True, "vision": True, "streaming": True},
    "gpt-4o-mini":    {"chat": True, "fc": True, "vision": True, "streaming": True},
    "gpt-4":          {"chat": True, "fc": True, "vision": False, "streaming": True},
    "gpt-3.5-turbo":  {"chat": True, "fc": True, "vision": False, "streaming": True},
    "gpt-3.5":        {"chat": True, "fc": False, "vision": False, "streaming": True},
    "o1":             {"chat": True, "fc": True, "vision": True, "streaming": False},
    "o3":             {"chat": True, "fc": True, "vision": True, "streaming": False},
    # Anthropic
    "claude-3":       {"chat": True, "fc": True, "vision": True, "streaming": True},
    "claude-3.5":     {"chat": True, "fc": True, "vision": True, "streaming": True},
    "claude-4":       {"chat": True, "fc": True, "vision": True, "streaming": True},
    # Google
    "gemini-2.0":     {"chat": True, "fc": True, "vision": True, "streaming": True},
    "gemini-2.5":     {"chat": True, "fc": True, "vision": True, "streaming": True},
    "gemini-3":       {"chat": True, "fc": True, "vision": True, "streaming": True},
    # Meta Llama
    "llama-2":        {"chat": True, "fc": False, "vision": False, "streaming": True},
    "llama-3":        {"chat": True, "fc": True, "vision": False, "streaming": True},
    "llama-3.1":      {"chat": True, "fc": True, "vision": False, "streaming": True},
    "llama-3.2":      {"chat": True, "fc": True, "vision": True, "streaming": True},
    "llama-3.3":      {"chat": True, "fc": True, "vision": False, "streaming": True},
    "llama-4":        {"chat": True, "fc": True, "vision": True, "streaming": True},
    # Mistral
    "mistral-7b":     {"chat": True, "fc": False, "vision": False, "streaming": True},
    "mistral-large":  {"chat": True, "fc": True, "vision": False, "streaming": True},
    "mistral-small":  {"chat": True, "fc": True, "vision": False, "streaming": True},
    "codestral":      {"chat": True, "fc": True, "vision": False, "streaming": True},
    "ministral":      {"chat": True, "fc": True, "vision": False, "streaming": True},
    "mixtral":        {"chat": True, "fc": True, "vision": False, "streaming": True},
    # DeepSeek
    "deepseek-v2":    {"chat": True, "fc": True, "vision": False, "streaming": True},
    "deepseek-v3":    {"chat": True, "fc": True, "vision": False, "streaming": True},
    "deepseek-v4":    {"chat": True, "fc": True, "vision": True, "streaming": True},
    "deepseek-coder": {"chat": True, "fc": True, "vision": False, "streaming": True},
    # Qwen
    "qwen-2":         {"chat": True, "fc": False, "vision": False, "streaming": True},
    "qwen-2.5":       {"chat": True, "fc": True, "vision": True, "streaming": True},
    "qwen-3":         {"chat": True, "fc": True, "vision": True, "streaming": True},
    # Step
    "step-3.5":       {"chat": True, "fc": True, "vision": True, "streaming": True},
    "step-3.7":       {"chat": True, "fc": True, "vision": True, "streaming": True},
    # NVIDIA Nemotron
    "nemotron":       {"chat": True, "fc": True, "vision": False, "streaming": True},
    # Autres
    "phi-3":          {"chat": True, "fc": False, "vision": True, "streaming": True},
    "phi-4":          {"chat": True, "fc": True, "vision": False, "streaming": True},
    "gemma-2":        {"chat": True, "fc": False, "vision": False, "streaming": True},
    "gemma-3":        {"chat": True, "fc": True, "vision": True, "streaming": True},
    "command-r":      {"chat": True, "fc": True, "vision": False, "streaming": True},
    "dbrx":           {"chat": True, "fc": False, "vision": False, "streaming": True},
    "aya":            {"chat": True, "fc": False, "vision": False, "streaming": True},
}


def _match_family(model_ref: str) -> Optional[Dict[str, Any]]:
    """Trouve la famille d'un modèle par son ref."""
    for family, caps in FAMILY_CAPABILITIES.items():
        if family in model_ref.lower():
            return caps
    return None


# ── API helpers ───────────────────────────────────────────────

def _api_get(url: str, headers: Dict[str, str] = None, timeout: int = 15) -> Optional[Any]:
    """Requête GET vers une API, retourne le JSON parsé ou None."""
    try:
        req = Request(url, headers=headers or {})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.debug(f"API error {url}: {e}")
        return None


# ── Sync depuis les données locales (base de connaissance) ─────

def sync_from_local(cat) -> int:
    """Remplit les capacités des modèles déjà dans le catalogue
    en utilisant la base de connaissance FAMILY_CAPABILITIES.

    Ne nécessite aucun appel API — utilisable immédiatement."""
    cat.conn.execute(CAPABILITIES_SCHEMA)
    rows = cat.conn.execute("""
        SELECT DISTINCT m.ref FROM catalogue_models m
        LEFT JOIN model_capabilities mc ON mc.model_ref = m.ref
        WHERE mc.model_ref IS NULL
    """).fetchall()

    count = 0
    for row in rows:
        ref = row["ref"]
        caps = _match_family(ref)
        if caps:
            _upsert_model(cat, ref, caps, source="knowledge")
            count += 1
    cat.conn.commit()
    return count


# ── Sync par provider ─────────────────────────────────────────

def sync_openai(cat, km) -> int:
    """Sync les modèles OpenAI."""
    key = km.get_key("openai")
    if not key:
        return 0
    api_key = key.get("api_key") if isinstance(key, dict) else key
    data = _api_get("https://api.openai.com/v1/models", {"Authorization": f"Bearer {api_key}"})
    if not data or "data" not in data:
        return 0
    count = 0
    for m in data["data"]:
        mid = m["id"]
        ref = f"openai/{mid}"
        caps = _match_family(mid) or {}
        _upsert_model(cat, ref, caps, source="api")
        _upsert_kem(cat, "openai", ref, mid)
        count += 1
    return count


def sync_nvidia(cat, km) -> int:
    """Sync les modèles NVIDIA."""
    key = km.get_key("nvidia")
    if not key:
        return 0
    api_key = key.get("api_key") if isinstance(key, dict) else key
    data = _api_get("https://integrate.api.nvidia.com/v1/models",
                    {"Authorization": f"Bearer {api_key}"})
    if not data:
        return 0
    models = data if isinstance(data, list) else data.get("data", [])
    count = 0
    for m in models:
        mid = m.get("id", m.get("name", ""))
        ref = f"nvidia/{mid}"
        caps = _match_family(mid) or {}
        _upsert_model(cat, ref, caps, source="api")
        _upsert_kem(cat, "nvidia", ref, mid)
        count += 1
    return count


def sync_groq(cat, km) -> int:
    """Sync les modèles Groq."""
    key = km.get_key("groq")
    if not key:
        return 0
    api_key = key.get("api_key") if isinstance(key, dict) else key
    data = _api_get("https://api.groq.com/openai/v1/models",
                    {"Authorization": f"Bearer {api_key}"})
    if not data or "data" not in data:
        return 0
    count = 0
    for m in data["data"]:
        mid = m["id"]
        ref = f"groq/{mid}"
        caps = _match_family(mid) or {}
        _upsert_model(cat, ref, caps, source="api")
        _upsert_kem(cat, "groq", ref, mid)
        count += 1
    return count


def sync_google(cat, km) -> int:
    """Sync les modèles Google Gemini."""
    key = km.get_key("google")
    if not key:
        return 0
    api_key = key.get("api_key") if isinstance(key, dict) else key
    data = _api_get("https://generativelanguage.googleapis.com/v1/models",
                    {"x-goog-api-key": api_key})
    if not data or "models" not in data:
        return 0
    count = 0
    for m in data["models"]:
        mid = m["name"].replace("models/", "")
        ref = f"google/{mid}"
        caps = _match_family(mid) or {}
        # Google API peut retourner des infos de capacité
        caps.update({
            "supports_chat": 1 if "chat" in str(m.get("supportedGenerationMethods", [])) else 0,
            "supports_embedding": 1 if "embedContent" in str(m.get("supportedGenerationMethods", [])) else 0,
        })
        _upsert_model(cat, ref, caps, source="api")
        _upsert_kem(cat, "google", ref, mid)
        count += 1
    return count


# ── Scraping HuggingFace ──────────────────────────────────────

def scrape_huggingface(model_ref: str) -> Optional[Dict[str, Any]]:
    """Scrape la page HuggingFace d'un modèle pour ses capacités.

    Utilisé en dernier recours quand l'API et la base de connaissance
    n'ont pas l'info."""
    import html.parser as hp

    url = f"https://huggingface.co/{model_ref}"
    html_content = _api_get(url, timeout=10)
    if not html_content:
        return None

    caps: Dict[str, Any] = {}
    text = str(html_content)

    # Détection basique par mots-clés dans le HTML
    if "conversational" in text.lower() or "chat template" in text.lower():
        caps["supports_chat"] = 1
    if "tool_use" in text.lower() or "function calling" in text.lower():
        caps["supports_function_calling"] = 1
    if "image" in text.lower() or "vision" in text.lower():
        caps["supports_vision"] = 1

    return caps if caps else None


# ── Mise à jour BDD ───────────────────────────────────────────

def _upsert_model(cat, ref: str, caps: Dict[str, Any], source: str = "unknown"):
    """Insère ou met à jour un modèle et ses capacités."""
    try:
        # S'assurer que la table des capacités existe
        cat.conn.execute(CAPABILITIES_SCHEMA)
        cat.conn.execute("""
            INSERT OR IGNORE INTO catalogue_models (ref, name)
            VALUES (?, ?)
        """, (ref, ref.split("/")[-1] if "/" in ref else ref))

        cat.conn.execute("""
            INSERT INTO model_capabilities
                (model_ref, supports_chat, supports_function_calling,
                 supports_vision, supports_embedding, supports_streaming, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_ref) DO UPDATE SET
                supports_chat = COALESCE(EXCLUDED.supports_chat, model_capabilities.supports_chat),
                supports_function_calling = COALESCE(EXCLUDED.supports_function_calling, model_capabilities.supports_function_calling),
                supports_vision = COALESCE(EXCLUDED.supports_vision, model_capabilities.supports_vision),
                supports_embedding = COALESCE(EXCLUDED.supports_embedding, model_capabilities.supports_embedding),
                supports_streaming = COALESCE(EXCLUDED.supports_streaming, model_capabilities.supports_streaming),
                source = CASE WHEN model_capabilities.source = 'unknown' THEN ? ELSE model_capabilities.source END,
                last_updated_at = strftime('%s','now')
        """, (
            ref,
            caps.get("chat", caps.get("supports_chat", 0)),
            caps.get("fc", caps.get("supports_function_calling", 0)),
            caps.get("vision", caps.get("supports_vision", 0)),
            caps.get("embedding", caps.get("supports_embedding", 0)),
            caps.get("streaming", caps.get("supports_streaming", 1)),
            source,  # VALUES.source
            source,  # CASE WHEN source
        ))
        cat.conn.commit()
    except Exception as e:
        logger.warning(f"Error upserting model {ref}: {e}")


def _upsert_kem(cat, provider_ref: str, model_ref: str, provider_model_name: str):
    """Insère ou met à jour provider_models_mapping."""
    try:
        cat.conn.execute("""
            INSERT OR IGNORE INTO provider_models_mapping
                (provider_id, model_id, provider_model_name, declared, available, last_checked_at)
            VALUES (
                (SELECT id FROM catalogue_providers WHERE ref = ?),
                (SELECT id FROM catalogue_models WHERE ref = ?),
                ?, 1, 1, strftime('%s','now')
            )
        """, (provider_ref, model_ref, provider_model_name))
        cat.conn.commit()
    except Exception as e:
        logger.debug(f"Error upserting KEM {provider_ref}/{model_ref}: {e}")


# ── Sync global ───────────────────────────────────────────────

SYNC_HANDLERS = {
    "openai": sync_openai,
    "nvidia": sync_nvidia,
    "groq": sync_groq,
    "google": sync_google,
}


def run_sync(cat, km, providers: Optional[List[str]] = None) -> Dict[str, int]:
    """Lance la synchronisation pour les providers spécifiés (ou tous).

    Retourne un dict {provider: count} du nombre de modèles synchronisés.
    """
    # Créer la table si elle n'existe pas
    cat.conn.execute(CAPABILITIES_SCHEMA)
    cat.conn.commit()

    results = {}
    targets = providers or list(SYNC_HANDLERS.keys())
    for prov in targets:
        handler = SYNC_HANDLERS.get(prov)
        if not handler:
            continue
        try:
            count = handler(cat, km)
            results[prov] = count
            logger.info(f"Sync {prov}: {count} modèles")
        except Exception as e:
            logger.error(f"Sync {prov} failed: {e}")
            results[prov] = -1

    return results