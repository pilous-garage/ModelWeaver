"""DirectBridge — Pont LLM direct via API OpenAI-compatible.

Remplace LiteLLM. Pour chaque provider, résout l'endpoint, la clé API
et le format du modèle depuis la base de données, puis appelle l'API
directement via HTTP.

Supporte :
  - Tous les providers OpenAI-compatibles (nvidia, groq, openrouter, together…)
  - Tool calling (function calling)
  - Classification d'erreurs (rate_limit, auth, context, timeout)
  - Streaming (futur)

Ne supporte pas encore :
  - Google Gemini (API différente)
  - Anthropic Claude (API différente)
"""

from __future__ import annotations

import json, os, time, urllib.request, urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Iterator
from urllib.parse import urljoin

import requests

from modules.llm_manager.base_bridge import (
    BaseBridge, BridgeError, ErrorCategory,
    ChatResponse, ModelCapabilities,
)


_USER_AGENT = "ModelWeaver/1.0 (+https://github.com/ModelWeaver)"

# ── Repos après échec d'un appel LLM (rate-limit / erreur) ──────────
# Quand un appel échoue, on marque le modèle « unavailable » et on ne le
# retente pas avant noretryuntil. La durée d'interdiction croît à chaque
# échec consécutif : notrytime = TIME_NO_RESTART_INIT × multiply^n.
TIME_NO_RESTART_INIT = 60.0         # durée initiale de repos (secondes)
TIME_NO_RESTART_MULTIPLY = 2.0      # croissance ×2 à chaque échec consécutif
TIME_NO_RESTART_MAX = 24 * 3600     # plafond (24 h)
# Repos pour crédit insuffisant (402) posé sur TOUT le provider : 1h au lieu
# de 24h — les crédits se rechargent souvent plus vite, et on préfère un
# provider re-testé 1h plus tard plutôt que bloqué la journée. Les vrais
# temps de reset seront extrapolés plus tard depuis model_call_log.
TIME_NO_RESTART_CREDIT_MAX = 3600.0
# Repos minimal pour un rate-limit RPM : les fenêtres de rate-limit sont de
# l'ordre de la minute MAIS dans un run swarm séquentiel, l'agent suivant
# alloue ~1-2 min après l'échec du précédent. Un repos de 30s laisse le
# modèle revenir dans le pool AVANT que le membre suivant n'alloue → il
# re-tombe dessus et re-rate-limite (boucle). On pose un minimum de 5 min
# pour que le modèle reste exclu pendant plusieurs membres successifs.
TIME_NO_RESTART_RPM_MIN = 300.0
# Plafond RPM DISTINCT du plafond général (24h) : un rate-limit RPM est
# transitoire (fenêtre de l'ordre de la minute), il se résorbe toujours en
# < 10 min. Sans ce plafond, l'incrémentation ×1.5 finit par atteindre 24h
# et tue google/openai pour la journée (RPM ≠ quota durable).
TIME_NO_RESTART_RPM_MAX = 10 * 60

# Retry automatique sur rate-limit (RPM bas) : on re-tente sur place si le
# retry_after est court, pour rester sur le même LLM (fenêtres de rate-limit
# de l'ordre de la minute).
RETRY_AUTO_DELAY_S = 2.0            # délai par défaut si retry_after inconnu
RETRY_AUTO_MAX_S = 15.0             # au-delà → laisser l'agent basculer

# Timeout HTTP global pour les probes/appels LLM : lisible depuis l'env
# ``MODELWEAVER_HTTP_TIMEOUT`` (secondes). Utilisé pour tous les appels réseau
# du bridge direct, y compris la découverte de modèles et les health checks.
# Valeur par défaut : 120s si la variable n'est pas définie.
try:
    HTTP_TIMEOUT = float(os.environ.get("MODELWEAVER_HTTP_TIMEOUT", "120"))
except (TypeError, ValueError):
    HTTP_TIMEOUT = 120.0

# Budget TOTAL pour un appel LLM (lecture complète de la réponse) : le timeout
# socket se réarme à chaque paquet reçu, donc une réponse qui dégouline pendant
# 9 min ne timeout jamais. Ce budget impose une échéance ABSOLUE sur la lecture.
# Lisible depuis l'env ``MODELWEAVER_LLM_BUDGET_S`` (défaut 60s) : au-delà, on
# abandonne l'appel (categoré timeout) → le thread agent se termine vite au
# lieu de rester bloqué → pas de purge P8 ni de re-spawn en boucle.
try:
    LLM_CALL_TOTAL_BUDGET_S = float(os.environ.get("MODELWEAVER_LLM_BUDGET_S", "60"))
except (TypeError, ValueError):
    LLM_CALL_TOTAL_BUDGET_S = 60.0

def _read_response_budgeted(resp, max_total_s: float = LLM_CALL_TOTAL_BUDGET_S,
                            chunk: int = 8192) -> bytes:
    """Lit la réponse complète avec une échéance ABSOLUE (pas un timeout socket
    réarmé). Lève socket.timeout si le budget est dépassé — l'appelant le
    traite comme un timeout réseau (bascule de provider)."""
    import socket as _socket
    deadline = time.time() + max_total_s
    parts = []
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise _socket.timeout(
                f"lecture réponse > {max_total_s:.0f}s (budget total dépassé)")
        try:
            b = resp.read(chunk)
        except _socket.timeout:
            # Timeout socket : seul si le budget le permet, on réessaie une
            # dernière lecture courte, sinon on abandonne.
            if time.time() >= deadline:
                raise
            raise
        if not b:
            break
        parts.append(b)
    return b"".join(parts)

def _load_provider_endpoints(cat) -> Dict[str, dict]:
    """Charge les endpoints et clés API depuis le catalogue DB.

    Retourne {provider_ref: {base_url, api_key, api_type}}
    """
    result = {}
    if not cat:
        return result

    try:
        rows = cat.conn.execute("""
            SELECT DISTINCT
                p.ref as provider_ref,
                pe.endpoint_url,
                pe.api_type,
                COALESCE(pe.api_type, p.api_type, 'openai') as effective_type,
                kem.key_ref
            FROM catalogue_providers p
            LEFT JOIN provider_endpoints pe ON pe.provider_id = p.id AND pe.is_default = 1
            LEFT JOIN provider_models_mapping kem ON kem.provider_id = p.id
            WHERE kem.key_ref IS NOT NULL
               OR p.provider_type = 'local'
        """).fetchall()
    except Exception:
        return result

    for row in rows:
        ref = row["provider_ref"]
        ep = result.get(ref, {})
        url = row["endpoint_url"] or _DEFAULT_ENDPOINTS.get(ref)
        if url:
            ep.setdefault("base_url", url)
        ep.setdefault("api_type", row["effective_type"] or "openai")
        if row["key_ref"]:
            ep.setdefault("api_key_refs", set()).add(row["key_ref"])
        result[ref] = ep

    # Charger les clés depuis l'env
    for ref, info in result.items():
        keys = _resolve_api_keys(ref, info.get("api_key_refs"))
        if keys:
            info["api_key"] = keys[0]

    # Providers ayant une clé API (modelweaver.db/api_keys) mais aucune ligne
    # provider_models_mapping : on ajoute leur endpoint par défaut pour qu'ils
    # soient découvrables/listables (sinon jamais interrogés).
    try:
        from modules.sql.db import ModelWeaverDB
        mw = ModelWeaverDB()
        try:
            key_rows = mw.conn.execute("""
                SELECT p.ref AS provider_ref
                FROM api_keys k
                JOIN providers p ON p.id = k.provider_id
            """).fetchall()
        finally:
            try:
                mw.close()
            except Exception:
                pass
        for kr in key_rows:
            ref = kr["provider_ref"]
            if ref == "gemini":
                ref = "google"
            if ref in result:
                continue
            ep_row = cat.conn.execute("""
                SELECT pe.endpoint_url, pe.api_type
                FROM provider_endpoints pe
                JOIN catalogue_providers p ON p.id = pe.provider_id
                WHERE p.ref = ? AND pe.is_default = 1
                LIMIT 1
            """, (ref,)).fetchone()
            url = (ep_row["endpoint_url"] if ep_row
                   else _DEFAULT_ENDPOINTS.get(ref))
            if not url:
                continue
            api_type = (ep_row["api_type"] if ep_row
                        else ("gemini" if ref == "google" else "openai"))
            ep = {"base_url": url, "api_type": api_type,
                  "api_key_refs": set()}
            keys = _resolve_api_keys(ref)
            if keys:
                ep["api_key"] = keys[0]
            result[ref] = ep
    except Exception:
        pass

    return result


_DEFAULT_ENDPOINTS = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "ollama": "http://localhost:11434/v1",
    "ollama-cloud": "https://ollama.com/v1",
    "github-models": "https://models.inference.ai.azure.com",
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "anthropic": "https://api.anthropic.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "huggingface": "https://api-inference.huggingface.co/v1",
}


_ENV_KEY_MAP = {
    "nvidia": ["NVIDIA_API_KEY", "NVAPI_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "together": ["TOGETHER_API_KEY"],
    "deepinfra": ["DEEPINFRA_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GEMINI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "github-models": ["GITHUB_TOKEN"],
    "huggingface": ["HUGGINGFACE_API_KEY", "HF_API_KEY"],
    "ollama-cloud": ["OLLAMA_API_KEY"],
}


def _resolve_api_keys(provider_ref: str, key_refs: Optional[set] = None) -> List[str]:
    """Résout les clés API pour un provider : env vars, .env, puis key_refs,
    puis toute clé du KeyManager (modelweaver.db) pour ce provider."""
    keys = []

    # Charger .env si présent (pour les providers sans env var exportée)
    _load_dotenv_once()

    for env_var in _ENV_KEY_MAP.get(provider_ref, []):
        val = os.environ.get(env_var)
        if val:
            keys.append(val)

    try:
        from modules.key_manager.key_manager import KeyManager
        from modules.sql.db import ModelWeaverDB
        km = KeyManager(ModelWeaverDB())
        if key_refs:
            for ref in key_refs:
                try:
                    val = km.get_key_by_ref(ref)
                    if val and val.get("api_key") not in keys:
                        keys.append(val["api_key"])
                except Exception:
                    pass
        else:
            # Toute clé du provider (table api_keys → provider)
            try:
                for row in km.db.keys.list_all():
                    if row.get("provider_ref") == provider_ref:
                        try:
                            val = km.get_key_by_ref(row["ref"])
                            if val and val.get("api_key") not in keys:
                                keys.append(val["api_key"])
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        pass

    return keys


_DOTENV_LOADED = False

def _load_dotenv_once():
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    # Chercher .env dans le CWD
    for path in (Path.cwd() / ".env", Path.home() / ".env"):
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip("\"'")
                if key not in os.environ:
                    os.environ[key] = val


# ── Construction du payload OpenAI-compatible ───────────────

def _build_messages(messages: List[Dict[str, str]],
                    system_prompt: Optional[str] = None) -> List[dict]:
    """Construit la liste de messages au format OpenAI."""
    result = list(messages)
    if system_prompt:
        has_system = any(m.get("role") == "system" for m in result)
        if not has_system:
            result.insert(0, {"role": "system", "content": system_prompt})
    return result


def _extract_tokens(usage: Optional[dict]) -> dict:
    """Normalise le comptage de tokens depuis un dict usage (tous formats).

    Gère les variantes de providers :
      - OpenAI / OpenRouter / la plupart : usage.prompt_tokens,
        usage.completion_tokens, usage.completion_tokens_details.reasoning_tokens
      - DeepSeek / reasoner : usage.reasoning_tokens (les reasoning tokens sont
        SÉPARÉS de completion_tokens chez certains providers)
      - Gemini : usageMetadata.promptTokenCount / candidatesTokenCount /
        thoughtsTokenCount
      - Some providers : completion_tokens_details.thinking_tokens

    Retourne {prompt, completion, thinking} — le total = prompt + completion
    (le thinking est INCLUS dans completion quand le provider ne le sépare pas,
    sinon on le met dans thinking et completion reste sans eux ; le champ
    `thinking` est TOUJOURS le nombre de tokens de raisonnement, déduit le cas
    échéant pour que le total soit complet sans double comptage).
    """
    u = usage or {}
    prompt = int(u.get("prompt_tokens") or u.get("promptTokenCount") or 0)
    completion = int(u.get("completion_tokens") or u.get("candidatesTokenCount") or 0)
    thinking = 0

    # Reasoning séparé (OpenAI / OpenRouter / DeepSeek).
    details = u.get("completion_tokens_details") or {}
    if details:
        thinking = int(details.get("reasoning_tokens") or details.get("thinking_tokens") or 0)
    if not thinking:
        thinking = int(u.get("reasoning_tokens") or 0)
    if not thinking:
        thinking = int(u.get("thoughtsTokenCount") or 0)
    # Certains providers mettent les reasoning à part SANS les retirer de
    # completion_tokens (double comptage) ; d'autres les retirent. On ne peut
    # pas le savoir — on garde thinking comme une INFO, le total reste
    # prompt + completion (le provider est la référence).
    return {"prompt": prompt, "completion": completion, "thinking": thinking}


def _build_tools_param(tools: Optional[List[Dict]] = None) -> Optional[List[dict]]:
    """Formate les outils au format OpenAI function calling strict.

    Deux formats entrant :
      - standard OpenAI : {type: function, function: {name, description, parameters}}
      - compact inspect_ai : {type: function, name, description, parameters} ?
        (les providers stricts — nvidia, deepseek — exigent le champ
        `function` imbriqué → "missing field 'function'". On normalise.)
    """
    if not tools:
        return None
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        t = dict(t)
        # format compact {type, name, description, parameters} → {type, function:{...}}
        if "function" not in t and t.get("name"):
            fn = {"name": t["name"]}
            if t.get("description"):
                fn["description"] = t["description"]
            if t.get("parameters"):
                fn["parameters"] = t["parameters"]
            t["function"] = fn
            t.pop("name", None)
            t.pop("description", None)
            t.pop("parameters", None)
        out.append(t)
    return out


def _build_model_id(provider_ref: str, model_ref: str) -> str:
    """Construit l'ID modèle pour l'API du provider.

    La plupart des providers OpenAI-compatibles acceptent le model_ref tel
    quel. Si la ref est préfixée par le provider lui-même (ex. openrouter/
    openai/gpt-5.2-chat pour provider openrouter), on retire le préfixe
    redondant pour envoyer openai/gpt-5.2-chat. Certains provider_model_name
    ont un préfixe DOUBLÉ (kilo/kilo/meta-llama/…) → on retire les préfixes
    provider répétés jusqu'à stabilité.
    """
    prev = None
    while model_ref != prev:
        prev = model_ref
        if model_ref.startswith(f"{provider_ref}/"):
            model_ref = model_ref[len(provider_ref) + 1:]
    return model_ref


# ── Adaptateur Cohere (chat natif, format non-OpenAI) ───────
# Cohere n'est PAS compatible OpenAI : son endpoint est /v1/chat (pas
# /v1/chat/completions) et le corps utilise `message` + `chat_history` +
# outils au format `parameter_definitions`. cf. point C (fix 405).

def _to_cohere_tools(openai_tools: Optional[List[Dict]]) -> Optional[List[dict]]:
    """Convertit des outils au format OpenAI function-calling en format Cohere."""
    if not openai_tools:
        return None
    out = []
    for t in openai_tools:
        fn = t.get("function", t)
        name = fn.get("name", "")
        desc = fn.get("description", "")
        params = fn.get("parameters", {}) or {}
        props = params.get("properties", {}) or {}
        required = params.get("required", []) or []
        pdefs = {}
        for pname, pspec in props.items():
            ptype = (pspec.get("type") or "string").lower()
            ctype = {
                "string": "string", "str": "string",
                "integer": "integer", "int": "integer",
                "number": "float", "float": "float",
                "boolean": "boolean", "bool": "boolean",
                "object": "object", "array": "array",
            }.get(ptype, "string")
            pdefs[pname] = {
                "type": ctype,
                "description": pspec.get("description", ""),
                "required": pname in required,
            }
        out.append({"name": name, "description": desc,
                    "parameter_definitions": pdefs})
    return out


def _cohere_build_body(provider_ref: str, model_ref: str,
                        messages: List[Dict[str, str]],
                        temperature: float, max_tokens: Optional[int],
                        system_prompt: Optional[str],
                        params: dict) -> dict:
    """Construit le corps d'une requête Cohere /v1/chat."""
    model_id = _build_model_id(provider_ref, model_ref)
    sys_msg = system_prompt
    history = []
    pending_user = None
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            if not sys_msg:
                sys_msg = content
            continue
        if role == "user":
            if pending_user is not None:
                history.append({"role": "USER", "message": pending_user})
            pending_user = content
        elif role == "assistant":
            history.append({"role": "CHATBOT", "message": content})
        elif role == "tool":
            history.append({"role": "TOOL", "message": content})
    if sys_msg:
        history.insert(0, {"role": "SYSTEM", "message": sys_msg})
    body = {"model": model_id, "message": pending_user or "",
            "chat_history": history, "temperature": temperature}
    if max_tokens:
        body["max_tokens"] = max_tokens
    tools = params.get("tools")
    if tools:
        body["tools"] = _to_cohere_tools(tools)
    return body


def _cohere_extract_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(p.get("text") or "")
            else:
                parts.append(str(p))
        return "".join(parts)
    return ""


def _cohere_parse_response(data: dict, provider_ref: str,
                           model_ref: str) -> "ChatResponse":
    """Parse une réponse Cohere /v1/chat en ChatResponse (format OpenAI)."""
    msg = (data or {}).get("message", {}) or {}
    content = _cohere_extract_content(msg.get("content"))
    tool_calls = []
    for tc in (msg.get("tool_calls") or []):
        if "function" in tc:
            fn = tc["function"]
            name = fn.get("name", "")
            args = fn.get("arguments", {})
        else:
            name = tc.get("name", "")
            args = tc.get("parameters", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                args = {}
        tool_calls.append({
            "id": "", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        })
    usage_raw = ((data or {}).get("usage") or {}).get("tokens") or {}
    usage = {
        "prompt_tokens": int(usage_raw.get("input_tokens") or 0),
        "completion_tokens": int(usage_raw.get("output_tokens") or 0),
    }
    return ChatResponse(
        content=content, model=(data or {}).get("model", model_ref),
        finish_reason="stop", usage=usage, raw=data,
        tool_calls=tool_calls or None)


# ── Classification des erreurs HTTP ────────────────────────

def _detect_limit_type(body: str, status: int, headers=None) -> Optional[str]:
    """Détecte le type de limite depuis le body / en-têtes d'erreur."""
    bl = (body or "").lower()
    if status == 429:
        # Quota journalier (ex. openrouter free-models-per-day, google
        # GenerateRequestsPerDayPerProjectPerModel-FreeTier) : ne se résout
        # pas en secondes → doit poser un repos LONG (24h), pas un backoff court.
        # Le message google « Quota exceeded for metric … PerDay … limit: 20 »
        # porte le marqueur camelCase « PerDay » (pas « per-day » avec tiret).
        if ("per-day" in bl or "daily" in bl or "per day" in bl or "day limit" in bl
                or "perday" in bl):
            return "daily_quota"
        # CRÉDIT/COMPTE À SEC ("no credits remaining", "insufficient_balance") :
        # problème PERMANENT (le compte n'a pas de fonds) → repos 24h + blacklist
        # du provider. On teste AVANT "rate"/"tokens" car ces messages peuvent
        # contenir "rate" ailleurs. Un 429 openai "You have no credits remaining"
        # n'est PAS un rate-limit réversible.
        if "credit" in bl or "balance" in bl or "insufficient" in bl:
            return "daily_quota"
        if "tokens" in bl or "token" in bl:
            return "tokens"
        if "rate" in bl or "requests" in bl or "rpm" in bl:
            return "rpm"
        # Un 429 "quota exceeded" (ex. google) est un RATE-LIMIT de fenêtre
        # (RPM), pas un quota durable — le quota se résorbe en minutes. Le
        # classer "quota" ferait grossir le repos ×1.5 sans plafond RPM
        # (5s → 36min → 24h) et exclurait le champion google pour rien.
        # Le VRAI quota durable (crédit insuffisant) est le 402/403.
        if "quota" in bl or "insufficient_quota" in bl or "credit" in bl or "balance" in bl:
            return "rpm"
        return "other"
    if status in (401, 403):
        return "quota" if ("quota" in bl or "credit" in bl or "balance" in bl) else "auth"
    return None


def _extract_retry_after(body: str, headers=None) -> Optional[float]:
    """Retourne retry_after (secondes) depuis l'en-tête Retry-After ou le body."""
    if headers:
        try:
            ra = headers.get("Retry-After") or headers.get("retry-after")
            if ra:
                return float(ra)
        except (TypeError, ValueError):
            pass
    import re
    m = re.search(r"retry[_ -]?after[^\d]{0,10}(\d+)", (body or "").lower())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # Message type "Please try again in 5 seconds"
    m = re.search(r"(?:again in|retry in|wait)\s+(\d+(?:\.\d+)?)\s*seconds?", (body or "").lower())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _classify_http_error(status: int, body: str,
                         provider_ref: str, model_ref: str,
                         headers=None) -> BridgeError:
    """Classe une erreur HTTP en BridgeError (enrichie de la limite)."""
    msg = body[:300] if body else f"HTTP {status}"
    limit_type = _detect_limit_type(body, status, headers)

    if status == 401 or status == 403:
        return BridgeError(ErrorCategory.AUTH, msg, provider_ref, model_ref,
                          limit_type=limit_type)

    # 402 Payment Required : crédits insuffisants (ex. openrouter sans crédits).
    # Problème de COMPTE = provider-wide (tous les modèles du provider sont
    # morts) → catégorie AUTH pour que le fallback blackliste le provider.
    if status == 402 or (status == 400 and "credit" in body.lower()):
        return BridgeError(ErrorCategory.AUTH, msg, provider_ref, model_ref,
                          limit_type="quota")

    if status == 429:
        retry_after = _extract_retry_after(body, headers)
        # "No credits remaining" = compte à sec → problème de COMPTE permanent
        # (provider-wide). Catégorie AUTH pour que le fallback blackliste le
        # provider entier (tous ses modèles sont morts) au lieu d'essayer chaque
        # modèle un par un. Le limit_type daily_quota pose déjà 24h de repos.
        if limit_type == "daily_quota" and ("credit" in (body or "").lower()
                                            or "balance" in (body or "").lower()
                                            or "insufficient" in (body or "").lower()):
            return BridgeError(ErrorCategory.AUTH, msg, provider_ref, model_ref,
                              retry_after_seconds=retry_after, limit_type="daily_quota")
        return BridgeError(ErrorCategory.RATE_LIMIT, msg, provider_ref, model_ref,
                          retry_after_seconds=retry_after, limit_type=limit_type)

    if status == 413 or status == 400 and "context" in body.lower():
        return BridgeError(ErrorCategory.CONTEXT, msg, provider_ref, model_ref,
                          limit_type=limit_type)

    if status == 408 or status == 504:
        return BridgeError(ErrorCategory.TIMEOUT, msg, provider_ref, model_ref,
                          limit_type=limit_type)

    if status >= 500:
        return BridgeError(ErrorCategory.SERVER, msg, provider_ref, model_ref,
                          limit_type=limit_type)

    return BridgeError(ErrorCategory.UNKNOWN, msg, provider_ref, model_ref,
                      limit_type=limit_type)


def _classify_exception(exc: Exception,
                        provider_ref: str, model_ref: str) -> BridgeError:
    """Classe une exception réseau en BridgeError."""
    msg = str(exc)[:300]

    if isinstance(exc, urllib.error.HTTPError):
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return _classify_http_error(exc.code, body, provider_ref, model_ref,
                                    headers=exc.headers)

    if isinstance(exc, urllib.error.URLError):
        return BridgeError(ErrorCategory.SERVER, f"Connection failed: {msg}",
                          provider_ref, model_ref)

    if isinstance(exc, TimeoutError):
        return BridgeError(ErrorCategory.TIMEOUT, msg, provider_ref, model_ref)

    return BridgeError(ErrorCategory.UNKNOWN, msg, provider_ref, model_ref)


# ── Bridge principal ────────────────────────────────────────

class DirectBridge(BaseBridge):
    """Pont LLM direct via API OpenAI-compatible.

    Remplace LiteLLMBridgeDefunct. Tous les providers OpenAI-compatibles sont
    supportés via la même implémentation. Les providers au format différent
    (Google, Anthropic) tombent sur leurs bridges natifs existants.
    """

    def __init__(self, cat=None, km=None):
        # Tout appel LLM doit être journalisé (model_call_log). Si aucun
        # catalogue n'est fourni, on en ouvre un pour que _log_call ne soit
        # jamais silencieusement ignoré (probes, sync, appels ponctuels).
        if cat is None:
            try:
                from modules.sql.catalogue_repo import CatalogueDB
                cat = CatalogueDB()
            except Exception:
                cat = None
        self.cat = cat
        self.km = km
        self._endpoints: Dict[str, dict] = {}

    # ── Cache d'endpoints ──────────────────────────────────

    def _get_endpoint(self, provider_ref: str) -> dict:
        """Retourne la config d'endpoint pour un provider (avec cache)."""
        if not self._endpoints:
            self._endpoints = _load_provider_endpoints(self.cat)

        ep = self._endpoints.get(provider_ref)
        if not ep:
            # Fallback : provider non trouvé dans la DB → essayer env vars
            keys = _resolve_api_keys(provider_ref)
            base_url = _DEFAULT_ENDPOINTS.get(provider_ref)
            if not base_url:
                base_url = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
            api_type = "openai"
            if provider_ref in ("google",):
                api_type = "gemini"
            elif provider_ref in ("anthropic",):
                api_type = "anthropic"
            ep = {"base_url": base_url, "api_key": keys[0] if keys else None,
                  "api_type": api_type}
            self._endpoints[provider_ref] = ep

        return ep

    # ── Repos après échec d'appel (noretryuntil / notrytime) ──

    def _mark_call_failed(self, provider_ref: str, model_ref: str,
                          limit_type: Optional[str] = None,
                          provider_wide: bool = False) -> None:
        """Marque un modèle « temporary unavailable » + pose un repos croissant.

        Appelé quand un appel LLM échoue (rate-limit, erreur réseau, 5xx…).
        Le modèle ne sera pas retenté avant `noretryuntil = now + notrytime`,
        et la durée d'interdiction croît à chaque échec consécutif.
        Un quota journalier (daily_quota) pose directement le repos maximal :
        il ne se résout pas en secondes, et garder un backoff court laisserait
        le modèle réallouable toute la journée (ex. openrouter free).
        `provider_wide=True` (402 crédit insuffisant, compte à sec) pose un
        repos modéré sur TOUT le provider : tous ses modèles sont morts.
        Best-effort : ne lève jamais (le chat doit rester fonctionnel).
        """
        if not self.cat:
            return
        try:
            now = time.time()
            duration = TIME_NO_RESTART_MAX if limit_type == "daily_quota" else None
            # La colonne provider_model_name est INCOHÉRENTE selon le provider :
            # certains stockent `provider/nom` (huggingface/deepseek-ai/…),
            # d'autres `nom` seul (nvidia/01-ai/…). Le model_ref passé ici est
            # souvent sans préfixe → le match exact rate la moitié des modèles
            # et les modèles morts ne sont JAMAIS marqués unavailable (re-alloués
            # en boucle). On matche les DEUX formes.
            prefixed = f"{provider_ref}/{model_ref}" if not model_ref.startswith(provider_ref + "/") else model_ref
            _match = (model_ref, prefixed)
            if duration is None:
                row = self.cat.conn.execute("""
                    SELECT notrytime FROM provider_models
                    WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                      AND (provider_model_name = ? OR provider_model_name = ?)
                """, (provider_ref, _match[0], _match[1])).fetchone()
                last = row["notrytime"] if row else 0.0
                duration = TIME_NO_RESTART_INIT if not last else last * TIME_NO_RESTART_MULTIPLY
                # Échec DÉFINITIF (404 Not Found, modèle disparu) : ne JAMAIS
                # re-tenter avant longtemps — un 404 ne se résout pas en minutes.
                if limit_type == "not_found":
                    duration = TIME_NO_RESTART_MAX
                # Rate-limit RPM : ne pas laisser un modèle revenir au pool après
                # 5s — la fenêtre RPM n'est pas résorbée (ordre de la minute).
                # Plafond RPM séparé : ne jamais dépasser 10 min (un RPM se
                # résorbe toujours vite ; 24h tuerait google/openai pour la
                # journée).
                if limit_type == "rpm":
                    duration = max(duration, TIME_NO_RESTART_RPM_MIN)
                    duration = min(duration, TIME_NO_RESTART_RPM_MAX)
                duration = min(duration, TIME_NO_RESTART_MAX)
            self.cat.conn.execute("""
                UPDATE provider_models
                SET unavailable = 1, noretryuntil = ?, notrytime = ?
                WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                  AND (provider_model_name = ? OR provider_model_name = ?)
            """, (now + duration, duration, provider_ref,
                  _match[0], _match[1]))
            # Problème de CRÉDIT (402 "insufficient credits/balance") : on
            # blackliste le provider UNIQUEMENT si c'est un vrai souci de COMPTE
            # — c.-à-d. si le MODÈLE échoué est free_tier=1 (le compte free est
            # à sec → tous les modèles free sont morts, comme openrouter). Si le
            # modèle est PAYANT, "insufficient balance" = juste CE modèle est
            # hors budget : les modèles free du provider (ex. llm7 : 4 free sur
            # 21) continuent de marcher → on n'exclut QUE ce modèle.
            if provider_wide:
                _pw = True
                try:
                    _r = self.cat.conn.execute("""
                        SELECT free_tier FROM provider_models
                        WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                          AND provider_model_name = ?
                    """, (provider_ref, model_ref)).fetchone()
                    if _r is not None and not _r["free_tier"]:
                        _pw = False  # modèle payant : ne pas blacklister le provider
                except Exception:
                    _pw = False
                if _pw:
                    self.cat.conn.execute("""
                        UPDATE provider_models
                        SET unavailable = 1, noretryuntil = ?, notrytime = ?
                        WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                    """, (now + TIME_NO_RESTART_CREDIT_MAX, TIME_NO_RESTART_CREDIT_MAX, provider_ref))
            try:
                self.cat.conn.commit()
            except Exception:
                pass
        except Exception:
            pass

    def _mark_call_ok(self, provider_ref: str, model_ref: str) -> None:
        """Reset le repos après un appel réussi (available=1, colonnes remises à 0)."""
        if not self.cat:
            return
        try:
            prefixed = f"{provider_ref}/{model_ref}" if not model_ref.startswith(provider_ref + "/") else model_ref
            self.cat.conn.execute("""
                UPDATE provider_models
                SET unavailable = 0, noretryuntil = 0, notrytime = 0
                WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                  AND (provider_model_name = ? OR provider_model_name = ?)
            """, (provider_ref, model_ref, prefixed))
            try:
                self.cat.conn.commit()
            except Exception:
                pass
        except Exception:
            pass

    def _log_call(self, provider_ref: str, model_ref: str, success: bool,
                  latency_ms: float, usage: Optional[dict] = None,
                  error_code: str = "", tokens_thinking: int = 0,
                  agent_id: Optional[str] = None,
                  error_msg: str = "", call_type: str = "chat",
                  caller_id: Optional[str] = None,
                  meta: Optional[dict] = None,
                  task_id: Optional[int] = None,
                  sub_task_id: Optional[int] = None) -> None:
        """Journalise un appel LLM réel dans model_call_log (métriques runtime).

        Référencé par ID (provider_id/model_id/provider_model_id), pas par nom.
        ``agent_id`` identifie l'agent appelant (None pour probes/health/sync).
        ``caller_id`` = SOURCE de l'appel (agent:N / bridge / service:X / probe).
          Permet de reconstruire les sessions PAR APPELLANT. Si non fourni,
          dérivé de agent_id (agent:<id>) sinon ``bridge``.
        ``error_code`` = catégorie (rate_limit/unknown/auth/...), ``error_msg`` =
        message brut tronqué (≤200 ch) pour l'analyse des patterns.
        ``call_type`` = chat / chat_stream / (futurs).
        Utilisé par le scoring d'allocation et les analyses de consommation.
        Best-effort : ne lève jamais.
        """
        if not self.cat:
            return
        try:
            u = usage or {}
            # Comptage normalisé (tous formats) : thinking déduit des champs
            # reasoning/thoughts des providers.
            toks = _extract_tokens(u)
            if not caller_id:
                caller_id = f"agent:{agent_id}" if agent_id else "bridge"
            meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
            # Lien structurel séquence→tâche : extrait des colonnes dédiées (si
            # non fournies directement, on les dérive du meta du FSM).
            if task_id is None:
                try:
                    task_id = int(meta.get("task_id")) if meta and meta.get("task_id") else None
                except (TypeError, ValueError):
                    task_id = None
            if sub_task_id is None:
                try:
                    sub_task_id = int(meta.get("sub_task_id")) if meta and meta.get("sub_task_id") else None
                except (TypeError, ValueError):
                    sub_task_id = None
            # model_id résolu en priorité via provider_models (le model_ref du
            # bridge = provider_model_name, ex. deepseek-ai/deepseek-v4-flash),
            # puis ref exact de catalogue_models, sinon 0 (non résolu → ignoré
            # par le scoring des buckets).
            _short = str(model_ref or "")
            if provider_ref and _short.startswith(f"{provider_ref}/"):
                _short = _short[len(provider_ref) + 1:]
            # adresse_id résolu une fois (réutilisé par l'INSERT ET la
            # consommation budget/scoring).
            _adresse_id = self.cat.conn.execute(
                "SELECT adresse_id FROM provider_model_address "
                "WHERE provider_ref = ? AND provider_model_name = ?",
                (provider_ref, _short)).fetchone()
            _adresse_id = int(_adresse_id["adresse_id"]) if _adresse_id else 0
            self.cat.conn.execute("""
                INSERT INTO model_call_log
                    (provider_id, model_id, provider_model_id, adresse_id, agent_id, success,
                     tokens_in, tokens_out, tokens_thinking, latency_ms,
                     error_code, error_msg, call_type, caller_id, meta_json,
                     task_id, sub_task_id)
                VALUES (
                    COALESCE((SELECT id FROM catalogue_providers WHERE ref = ?), 0),
                    COALESCE(
                        (SELECT pm.model_id FROM provider_models pm
                          JOIN catalogue_providers p ON p.id = pm.provider_id
                         WHERE p.ref = ? AND pm.provider_model_name = ?),
                        (SELECT id FROM catalogue_models WHERE ref = ?),
                        0),
                    (SELECT pm.id FROM provider_models pm
                      JOIN catalogue_providers p ON p.id = pm.provider_id
                     WHERE p.ref = ? AND pm.provider_model_name = ?),
                    ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (provider_ref, provider_ref, _short, model_ref,
                  provider_ref, _short,
                  _adresse_id,
                  (str(agent_id)[:80] if agent_id else None),
                  int(success), toks["prompt"], toks["completion"],
                  toks["thinking"] or int(tokens_thinking or 0),
                  float(latency_ms or 0), (error_code or "")[:100],
                  (error_msg or "")[:200], (call_type or "chat")[:30],
                  (str(caller_id)[:120] if caller_id else None), meta_json,
                  int(task_id) if task_id else None,
                  int(sub_task_id) if sub_task_id else None))
            self.cat.conn.commit()
            # JONCTION Idée 18 : consommation budget/cost + états d'erreur +
            # scoring de fiabilité — synchrone à l'appel (régulation temps réel).
            try:
                from services.llm_usage.consume import consume_call
                consume_call(
                    self.cat, _adresse_id, bool(success),
                    tokens_in=toks["prompt"], tokens_out=toks["completion"],
                    tokens_thinking=toks["thinking"] or int(tokens_thinking or 0),
                    latency_ms=float(latency_ms or 0),
                    error_code=error_code or "",
                    agent_id=agent_id,
                    task_id=task_id, sub_task_id=sub_task_id,
                    nb_requetes=1)
            except Exception:
                # La consommation est best-effort : un souci ici ne casse pas
                # l'appel (le log détaillé est déjà écrit).
                try:
                    self.cat.conn.rollback()
                except Exception:
                    pass
            # Les lignes détaillées sont agrégées par le TICKER DE BATCHAGE
            # (usage_batcher, service séparé) : il lit model_call_log par
            # fenêtres de temps, alimente les tables d'agrégats (1m/15m/3h/…)
            # en cascade, puis supprime les lignes détaillées > 5 min. On ne
            # borne plus ici (10k) — le batcheur fait le ménage, et le détail
            # récent reste dispo pour recent_llm.
        except Exception:
            try:
                self.cat.conn.rollback()
            except Exception:
                pass

    # ── Chat ───────────────────────────────────────────────

    def _auto_retry_rate_limit(self, req: urllib.request.Request,
                               err: BridgeError,
                               max_retries: int = 3) -> Optional[dict]:
        """Retry automatique sur rate-limit à RPM bas.

        Quand l'API répond 429 (rate-limit), on re-tente sur place si le
        retry_after est court (≤ RETRY_AUTO_MAX_S) : rester sur le même LLM
        est préférable pour les modèles à RPM bas mais volume horaire haut.
        Retourne le JSON de la réponse si retry réussi, sinon None.
        """
        if err.category != ErrorCategory.RATE_LIMIT:
            return None
        if err.limit_type in ("quota", "daily_quota"):
            return None  # solde/quota journalier épuisé : inutile de re-tenter
        retry_after = err.retry_after_seconds
        if retry_after is None:
            retry_after = RETRY_AUTO_DELAY_S
        if retry_after > RETRY_AUTO_MAX_S:
            return None  # trop long : laisser l'agent décider (bascule)
        for _ in range(max_retries):
            time.sleep(min(retry_after, RETRY_AUTO_MAX_S))
            try:
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                    return json.loads(_read_response_budgeted(resp).decode())
            except Exception as exc:
                retry_after = retry_after * 2  # backoff doux sur place
        return None

    def chat(self, provider_ref: str, model_ref: str,
             messages: List[Dict[str, str]],
             temperature: float = 0.7,
             max_tokens: Optional[int] = None,
             system_prompt: Optional[str] = None,
             stream: bool = False,
             agent_id: Optional[str] = None,
             **params) -> ChatResponse:
        """Appelle l'API du provider et retourne la réponse.

        ``agent_id`` identifie l'agent appelant (traçé dans model_call_log ;
        None pour probes/health/sync).
        """
        params["agent_id"] = agent_id
        params.setdefault("call_type", "chat_stream" if stream else "chat")
        # Source de l'appel : dérivé de agent_id si fourni, sinon un identifiant
        # de contexte explicite (bridge/service/probe) passé via caller_id.
        if "caller_id" not in params:
            params["caller_id"] = f"agent:{agent_id}" if agent_id else "bridge"
        if stream:
            return self._chat_stream_internal(provider_ref, model_ref,
                                              messages, temperature,
                                              max_tokens, system_prompt, **params)

        ep = self._get_endpoint(provider_ref)

        # Routage selon le type d'API
        api_type = ep.get("api_type", "openai")
        if api_type == "gemini":
            return self._google_chat(ep, provider_ref, model_ref, messages,
                                     temperature, max_tokens, system_prompt, **params)
        if api_type == "cohere":
            return self._cohere_chat(ep, provider_ref, model_ref, messages,
                                     temperature, max_tokens, system_prompt,
                                     stream=stream, **params)

        # OpenAI-compatible
        model_id = _build_model_id(provider_ref, model_ref)
        msgs = _build_messages(messages, system_prompt)

        url = urljoin(ep["base_url"].rstrip("/") + "/", "chat/completions")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ep.get('api_key', '')}" if ep.get('api_key') else "",
            "User-Agent": _USER_AGENT,
        }

        body = {
            "model": model_id,
            "messages": msgs,
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens

        tools = params.get("tools")
        if tools:
            body["tools"] = _build_tools_param(tools)

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={k: v for k, v in headers.items() if v},
            method="POST",
        )

        _t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                data = json.loads(_read_response_budgeted(resp).decode())
            self._log_call(provider_ref, model_ref, True,
                           (time.time() - _t0) * 1000.0,
                           usage=(data or {}).get("usage"),
                           agent_id=params.get("agent_id"),
                           caller_id=params.get("caller_id"),
                           call_type=params.get("call_type", "chat"),
                           meta=params.get("meta"))
        except Exception as exc:
            err = _classify_exception(exc, provider_ref, model_ref)
            _err_cat = getattr(err, "category", None)
            self._mark_call_failed(provider_ref, model_ref, limit_type=err.limit_type,
                                   provider_wide=(_err_cat is not None
                                                  and getattr(_err_cat, "value", "") == "auth"))
            retried = self._auto_retry_rate_limit(req, err)
            if retried is not None:
                data = retried
            else:
                _cat = getattr(err, "category", None)
                _msg = getattr(err, "message", "") or str(exc)
                self._log_call(provider_ref, model_ref, False,
                               (time.time() - _t0) * 1000.0,
                               error_code=_cat.value if _cat else str(err)[:100],
                               error_msg=_msg,
                               agent_id=params.get("agent_id"),
                               caller_id=params.get("caller_id"),
                               call_type=params.get("call_type", "chat"),
                               meta=params.get("meta"))
                raise err

        self._mark_call_ok(provider_ref, model_ref)
        return self._parse_response(data, provider_ref, model_ref)

    def _google_chat(self, ep: dict, provider_ref: str, model_ref: str,
                     messages: List[Dict[str, str]],
                     temperature: float, max_tokens: Optional[int],
                     system_prompt: Optional[str] = None,
                     **params) -> ChatResponse:
        """Appelle l'API Google Gemini (format différent d'OpenAI)."""
        base = ep.get("base_url", "https://generativelanguage.googleapis.com/v1beta")
        api_key = ep.get("api_key") or ""
        # L'API Gemini attend le NOM du modèle sans préfixe provider redondant
        # (google/gemini-3.5-flash → gemini-3.5-flash). Sans ça l'API renvoie
        # "model not found" (ex. google/google/gemini-3.5-flash).
        model_id = _build_model_id(provider_ref, model_ref)
        url = f"{base.rstrip('/')}/models/{model_id}:generateContent"

        # Convertir les messages au format Gemini
        contents = []
        system_instruction = None
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_instruction = {"parts": [{"text": content}]}
                continue
            if role == "tool":
                # Réponse à un functionCall : format Gemini = fonctionResponse
                # (rôle implicite, à l'intérieur de parts).
                try:
                    payload = json.loads(content) if isinstance(content, str) else content
                except Exception:
                    payload = {"output": content}
                tool_name = m.get("name") or m.get("tool_name") or "function"
                fr = {"name": tool_name, "response": {"result": payload}}
                if m.get("tool_call_id"):
                    fr["id"] = m["tool_call_id"]
                contents.append({
                    "role": "model",
                    "parts": [{"functionResponse": fr}],
                })
                continue
            if role == "assistant":
                gemini_role = "model"
                # Assistant avec functionCalls : le message précédent a déclenché
                # des outils. On les rejoue en parts functionCall.
                tcs = m.get("tool_calls")
                if tcs:
                    parts = []
                    if content:
                        parts.append({"text": content})
                    for tc in tcs:
                        fn = tc.get("function", {})
                        try:
                            args = json.loads(fn.get("arguments", "{}"))
                        except Exception:
                            args = {}
                        fc_part = {"name": fn.get("name", ""),
                                   "args": args,
                                   "id": tc.get("id") or ""}
                        parts.append({"functionCall": fc_part})
                        # Gemini exige la thoughtSignature au niveau du part.
                        ts = tc.get("thoughtSignature") or fn.get("thoughtSignature") or ""
                        if ts:
                            parts[-1]["thoughtSignature"] = ts
                        elif provider_ref == "google":
                            import sys as _sys
                            print(f"[google_chat] functionCall SANS thoughtSignature: "
                                  f"{fn.get('name')} id={tc.get('id')!r} "
                                  f"tour_messages={len(contents)}", file=_sys.stderr, flush=True)
                    contents.append({"role": gemini_role, "parts": parts})
                    continue
            else:
                gemini_role = role
            contents.append({
                "role": gemini_role,
                "parts": [{"text": content}],
            })

        if system_prompt and not system_instruction:
            system_instruction = {"parts": [{"text": system_prompt}]}

        body = {"contents": contents}
        if system_instruction:
            body["systemInstruction"] = system_instruction

        generation_config = {"temperature": temperature}
        if max_tokens:
            generation_config["maxOutputTokens"] = max_tokens
        body["generationConfig"] = generation_config

        # Tools (function calling) au format Gemini
        tools = params.get("tools")
        if tools:
            gemini_declarations = []
            for t in tools:
                if t.get("type") == "function":
                    fn = t.get("function", {})
                    gemini_declarations.append({
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {}),
                    })
            if gemini_declarations:
                body["tools"] = [{"functionDeclarations": gemini_declarations}]

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
            "User-Agent": _USER_AGENT,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={k: v for k, v in headers.items() if v},
            method="POST",
        )

        _t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                data = json.loads(_read_response_budgeted(resp).decode())
            _gm_usage = (data or {}).get("usageMetadata", {})
            self._log_call(
                provider_ref, model_ref, True,
                (time.time() - _t0) * 1000.0,
                usage={
                    "prompt_tokens": _gm_usage.get("promptTokenCount", 0),
                    "completion_tokens": _gm_usage.get("candidatesTokenCount", 0),
                },
                agent_id=params.get("agent_id"),
                caller_id=params.get("caller_id"),
                call_type=params.get("call_type", "chat"))
        except Exception as exc:
            err = _classify_exception(exc, provider_ref, model_ref)
            _err_cat = getattr(err, "category", None)
            self._mark_call_failed(provider_ref, model_ref, limit_type=err.limit_type,
                                   provider_wide=(_err_cat is not None
                                                  and getattr(_err_cat, "value", "") == "auth"))
            retried = self._auto_retry_rate_limit(req, err)
            if retried is not None:
                data = retried
            else:
                _cat = getattr(err, "category", None)
                _msg = getattr(err, "message", "") or str(exc)
                self._log_call(provider_ref, model_ref, False,
                               (time.time() - _t0) * 1000.0,
                               error_code=_cat.value if _cat else str(err)[:100],
                               error_msg=_msg,
                               agent_id=params.get("agent_id"),
                               caller_id=params.get("caller_id"),
                               call_type=params.get("call_type", "chat"))
                raise err

        self._mark_call_ok(provider_ref, model_ref)
        return self._parse_google_response(data, provider_ref, model_ref)

    @staticmethod
    def _parse_google_response(data: dict, provider_ref: str,
                                model_ref: str) -> ChatResponse:
        """Parse une réponse JSON Google Gemini en ChatResponse."""
        candidate = (data.get("candidates") or [{}])[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        text_parts = []
        tool_calls = None
        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                fc = part["functionCall"]
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append({
                    "id": fc.get("id") or fc.get("name", "fc_0"),
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                    # Gemini renvoie la thoughtSignature au niveau du part
                    # (obligatoire quand on rejoue les functionCall ensuite).
                    "thoughtSignature": part.get("thoughtSignature", ""),
                })

        finish_reason = candidate.get("finishReason", "stop").lower()
        usage = data.get("usageMetadata", {})

        return ChatResponse(
            content="".join(text_parts),
            model=data.get("modelVersion") or model_ref,
            finish_reason=finish_reason,
            usage={
                "prompt_tokens": usage.get("promptTokenCount", 0),
                "completion_tokens": usage.get("candidatesTokenCount", 0),
                "total_tokens": usage.get("totalTokenCount", 0),
            },
            raw=data,
            tool_calls=tool_calls,
        )

    def chat_stream(self, provider_ref: str, model_ref: str,
                    messages: List[Dict[str, str]],
                    temperature: float = 0.7,
                    max_tokens: Optional[int] = None,
                    system_prompt: Optional[str] = None,
                    **params) -> Iterator[str]:
        """Flux de contenu texte seulement (compat) : délégué à chat_stream_events.

        Les modèles raisonneurs émettent d'abord des deltas `thinking` puis des
        deltas `content` — on ne renvoie ici QUE la partie content (interfaces
        qui ne veulent pas le reasoning).
        """
        for ev in self.chat_stream_events(
            provider_ref, model_ref, messages,
            temperature=temperature, max_tokens=max_tokens,
            system_prompt=system_prompt, **params,
        ):
            if ev.get("type") == "content":
                yield ev["delta"]

    def chat_stream_events(self, provider_ref: str, model_ref: str,
                           messages: List[Dict[str, str]],
                           temperature: float = 0.7,
                           max_tokens: Optional[int] = None,
                           system_prompt: Optional[str] = None,
                           **params) -> Iterator[Dict[str, Any]]:
        """Flux SSE réel (OpenAI-compatible) avec emissions thinking/content.

        Yield des dicts : {"type": "thinking"|"content", "delta": str}. Les
        modèles raisonneurs (deepseek-v4-flash, etc.) émettent d'abord des
        deltas "thinking" puis la réponse dans "content".

        Gemini (api_type=gemini) : converti en flux via le même contrat
        (cf. _google_chat pour le format de requête Gemini).
        """
        ep = self._get_endpoint(provider_ref)
        api_type = ep.get("api_type", "openai")
        if api_type == "gemini":
            yield from self._google_chat_stream(ep, provider_ref, model_ref,
                                                messages, temperature,
                                                max_tokens, system_prompt,
                                                **params)
            return
        if api_type == "cohere":
            yield from self._cohere_chat_stream_events(
                ep, provider_ref, model_ref, messages, temperature,
                max_tokens, system_prompt, **params)
            return

        # Source de l'appel pour la session par caller (appel direct, sans passer
        # par chat() : on dérive de agent_id sinon bridge).
        params.setdefault("caller_id",
                          f"agent:{params.get('agent_id')}" if params.get("agent_id") else "bridge")
        model_id = _build_model_id(provider_ref, model_ref)
        msgs = _build_messages(messages, system_prompt)

        url = urljoin(ep["base_url"].rstrip("/") + "/", "chat/completions")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ep.get('api_key', '')}" if ep.get('api_key') else "",
            "User-Agent": _USER_AGENT,
        }
        body = {
            "model": model_id,
            "messages": msgs,
            "temperature": temperature,
            "stream": True,
            # Demande le usage en fin de flux (OpenAI/OpenRouter/etc.) pour
            # comptabiliser les tokens des appels en streaming (sinon 0).
            "stream_options": {"include_usage": True},
        }
        if max_tokens:
            body["max_tokens"] = max_tokens

        tools = params.get("tools")
        if tools:
            body["tools"] = _build_tools_param(tools)

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={k: v for k, v in headers.items() if v},
            method="POST",
        )

        _t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                usage_meta = None
                for line in resp:
                    line = line.strip()
                    if not line or not line.startswith(b"data:"):
                        continue
                    payload = line[len(b"data:"):].strip()
                    if payload in (b"[DONE]", b"[done]"):
                        break
                    try:
                        chunk = json.loads(payload.decode("utf-8", "replace"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    # Dernier chunk (stream_options.include_usage) : porte le
                    # usage final — on le garde pour le log.
                    if chunk.get("usage"):
                        usage_meta = chunk["usage"]
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta", {}) or {}
                    d_content = delta.get("content") or ""
                    d_reason = delta.get("reasoning_content") or ""
                    if d_reason:
                        yield {"type": "thinking", "delta": d_reason}
                    if d_content:
                        yield {"type": "content", "delta": d_content}
                    for tcf in (delta.get("tool_calls") or []):
                        yield {"type": "tool_calls", "delta": tcf}
            self._log_call(provider_ref, model_ref, True,
                           (time.time() - _t0) * 1000.0,
                           usage=usage_meta,
                           agent_id=params.get("agent_id"),
                           caller_id=params.get("caller_id"),
                           call_type="chat_stream")
        except Exception as exc:
            err = _classify_exception(exc, provider_ref, model_ref)
            _cat = getattr(err, "category", None)
            self._log_call(provider_ref, model_ref, False,
                           (time.time() - _t0) * 1000.0,
                           error_code=_cat.value if _cat else str(err)[:100],
                           error_msg=str(err)[:200],
                           agent_id=params.get("agent_id"),
                           caller_id=params.get("caller_id"),
                           call_type="chat_stream")
            raise err

    def _chat_stream_internal(self, *args, **kwargs) -> ChatResponse:
        """Équivalent non-itérateur : agrège le flux en ChatResponse."""
        provider_ref = args[0] if args else kwargs.get("provider_ref")
        model_ref = args[1] if len(args) > 1 else kwargs.get("model_ref")
        if not provider_ref or not model_ref:
            raise ValueError("_chat_stream_internal: provider_ref/model_ref requis")
        content_chunks: List[str] = []
        for ev in self.chat_stream_events(*args, **kwargs):
            if ev.get("type") == "content":
                content_chunks.append(ev.get("delta", ""))
        content = "".join(content_chunks)
        return ChatResponse(
            content=content,
            model=model_ref,
            finish_reason="stop",
            usage={},
        )

    def _cohere_chat(self, ep: dict, provider_ref: str, model_ref: str,
                     messages: List[Dict[str, str]],
                     temperature: float = 0.7, max_tokens: Optional[int] = None,
                     system_prompt: Optional[str] = None,
                     stream: bool = False, **params) -> ChatResponse:
        """Chat via l'API native Cohere (/v1/chat). cf. point C (fix 405)."""
        if stream:
            # Pas de SSE natif simple : on appelle la variante non-stream.
            return self._chat_stream_internal(provider_ref, model_ref,
                                              messages, temperature,
                                              max_tokens, system_prompt, **params)
        params["agent_id"] = params.get("agent_id")
        params.setdefault("call_type", "chat")
        api_key = ep.get("api_key", "")
        url = ep["base_url"].rstrip("/") + "/chat"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}" if api_key else "",
            "User-Agent": _USER_AGENT,
        }
        body = _cohere_build_body(provider_ref, model_ref, messages,
                                  temperature, max_tokens, system_prompt, params)
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={k: v for k, v in headers.items() if v}, method="POST")
        _t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                data = json.loads(_read_response_budgeted(resp).decode())
            self._log_call(provider_ref, model_ref, True,
                           (time.time() - _t0) * 1000.0,
                           agent_id=params.get("agent_id"),
                           caller_id=params.get("caller_id"),
                           call_type=params.get("call_type", "chat"))
        except Exception as exc:
            err = _classify_exception(exc, provider_ref, model_ref)
            self._mark_call_failed(
                provider_ref, model_ref,
                limit_type=err.limit_type,
                provider_wide=(getattr(err.category, "value", "") == "auth"))
            retried = self._auto_retry_rate_limit(req, err)
            if retried is not None:
                data = retried
            else:
                self._log_call(
                    provider_ref, model_ref, False,
                    (time.time() - _t0) * 1000.0,
                    error_code=getattr(err.category, "value", ""),
                    error_msg=str(exc)[:200],
                    agent_id=params.get("agent_id"),
                    caller_id=params.get("caller_id"),
                    call_type=params.get("call_type", "chat"))
                raise err
        self._mark_call_ok(provider_ref, model_ref)
        return _cohere_parse_response(data, provider_ref, model_ref)

    def _cohere_chat_stream_events(self, ep: dict, provider_ref: str,
                                   model_ref: str, messages: List[Dict[str, str]],
                                   temperature: float, max_tokens: Optional[int],
                                   system_prompt: Optional[str] = None,
                                   **params) -> Iterator[Dict[str, Any]]:
        """Streaming Cohere : on appelle la variante non-stream et on émet le
        contenu + les tool_calls sous forme d'événements (même contrat)."""
        resp = self._cohere_chat(ep, provider_ref, model_ref, messages,
                                  temperature, max_tokens, system_prompt,
                                  stream=False, **params)
        if resp and resp.content:
            yield {"type": "content", "delta": resp.content}
        for tc in (resp.tool_calls or []):
            yield {"type": "tool_calls", "delta": tc}

    def _google_chat_stream(self, ep: dict, provider_ref: str, model_ref: str,
                            messages: List[Dict[str, str]],
                            temperature: float, max_tokens: Optional[int],
                            system_prompt: Optional[str] = None,
                            **params) -> Iterator[Dict[str, Any]]:
        """Gemini streaming via generateContent?alt=sse — même contrat (dicts)."""
        base = ep.get("base_url", "https://generativelanguage.googleapis.com/v1beta")
        api_key = ep.get("api_key") or ""
        url = f"{base.rstrip('/')}/models/{model_ref}:streamGenerateContent?alt=sse"

        contents = []
        system_instruction = None
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_instruction = {"parts": [{"text": content}]}
                continue
            if role == "tool":
                try:
                    payload = json.loads(content) if isinstance(content, str) else content
                except Exception:
                    payload = {"output": content}
                contents.append({
                    "role": "model",
                    "parts": [{"functionResponse": {"name": m.get("name") or "function",
                                                     "response": {"result": payload}}}],
                })
                continue
            if role == "assistant":
                parts = []
                if content:
                    parts.append({"text": content})
                # Les functionCall/tool_calls ne sont pas rejoués en streaming
                # (un seul appel de génération) : les outils précédents sont
                # déjà rejoués via _google_chat en mode non-stream.
                contents.append({"role": "model", "parts": parts})
                continue
            contents.append({"role": "user" if role == "user" else role,
                             "parts": [{"text": content}]})

        body = {"contents": contents,
                "generationConfig": {"temperature": temperature}}
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if max_tokens:
            body["generationConfig"]["maxOutputTokens"] = max_tokens

        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {api_key}"} if api_key else {})},
            method="POST",
        )
        _t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                buf = ""
                usage_meta = None
                for raw in resp:
                    buf += raw.decode("utf-8", "replace")
                    while "\n\n" in buf:
                        evt_block, buf = buf.split("\n\n", 1)
                        data_line = ""
                        for line in evt_block.splitlines():
                            if line.startswith("data:"):
                                data_line += line[5:].strip()
                        if not data_line:
                            continue
                        try:
                            data = json.loads(data_line)
                        except json.JSONDecodeError:
                            continue
                        # usageMetadata porté par le dernier chunk.
                        if data.get("usageMetadata"):
                            usage_meta = data["usageMetadata"]
                        for cand in data.get("candidates", []):
                            for part in (cand.get("content") or {}).get("parts", []) or []:
                                if "text" in part:
                                    yield {"type": "content", "delta": part["text"]}
                                elif "thought" in part:
                                    yield {"type": "thinking", "delta": part.get("thought", "")}
            self._log_call(provider_ref, model_ref, True,
                           (time.time() - _t0) * 1000.0,
                           usage=usage_meta,
                           agent_id=params.get("agent_id"),
                           caller_id=params.get("caller_id"),
                           call_type="chat_stream")
        except Exception as exc:
            err = _classify_exception(exc, provider_ref, model_ref)
            self._log_call(provider_ref, model_ref, False,
                           (time.time() - _t0) * 1000.0,
                           error_code=str(err)[:100], error_msg=str(err)[:200],
                           agent_id=params.get("agent_id"),
                           caller_id=params.get("caller_id"),
                           call_type="chat_stream")
            raise err

    # ── Parsing réponse ────────────────────────────────────

    @staticmethod
    def _parse_response(data: dict, provider_ref: str,
                        model_ref: str) -> ChatResponse:
        """Parse une réponse JSON au format OpenAI en ChatResponse."""
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})

        content = message.get("content") or ""
        tool_calls_raw = message.get("tool_calls")
        # Modèles raisonneurs (deepseek-v4-flash, etc.) : mettent le texte dans
        # `reasoning_content` et renvoient `content` vide. On retombe dessus
        # pour ne pas renvoyer une « réponse vide » au workflow.
        if not content and not tool_calls_raw:
            content = message.get("reasoning_content") or ""

        tool_calls = None
        if tool_calls_raw:
            tool_calls = []
            for tc in tool_calls_raw:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "type": tc.get("type", "function"),
                    "function": {
                        "name": fn.get("name", ""),
                        "arguments": json.dumps(args),
                    },
                })

        usage = data.get("usage", {})
        finish_reason = choice.get("finish_reason", "stop")

        return ChatResponse(
            content=content,
            model=data.get("model", model_ref),
            finish_reason=finish_reason,
            usage=usage,
            raw=data,
            tool_calls=tool_calls,
        )

    # ── Capacités ──────────────────────────────────────────

def get_capabilities(self, provider_ref: str,
                     model_ref: str) -> ModelCapabilities:
        """Retourne les capacités depuis la base de données model_capability
        (nouvelle schema) ou valeurs par défaut.

        Agrège toutes les lignes model_capability pour le modèle, priorité
        source : official > user > enterprise > models.dev > distant/friend/git.
        """
        if not self.cat:
            return ModelCapabilities(
                supports_function_calling=True,
                supports_vision=False,
                context_window=4096, max_output=4092,
            )

        try:
            rows = self.cat.conn.execute("""
                SELECT mc.capability, mc.value, mc.confidence, mc.source_ref
                FROM model_capability mc
                JOIN catalogue_models cm ON cm.id = mc.model_id
                WHERE cm.ref = ? OR cm.model_key = ?
                ORDER BY
                    CASE mc.source_ref
                        WHEN 'official' THEN 1
                        WHEN 'user' THEN 2
                        WHEN 'enterprise' THEN 3
                        WHEN 'models.dev' THEN 4
                        ELSE 5
                    END,
                    mc.confidence DESC
            """, (model_ref, model_ref)).fetchall()

            if not rows:
                return ModelCapabilities(
                    supports_function_calling=True,
                    supports_vision=False,
                    context_window=4096, max_output=4092,
                )

            # Agrégation : prendre la source la plus prioritaire avec value='true' et conf>0.5
            sfc = False  # supports_function_calling
            sv = False   # supports_vision

            for cap, value, confidence, source_ref in rows:
                if value == "true" and confidence > 0.5:
                    if cap == "supports_function_calling":
                        sfc = True
                    elif cap == "supports_vision":
                        sv = True

            return ModelCapabilities(
                supports_function_calling=sfc,
                supports_vision=sv,
                context_window=4096, max_output=4092,
            )
        except Exception:
            pass

        # Fallback : defaults pour modèles récents
        return ModelCapabilities(
            supports_function_calling=True,
            supports_vision=False,
            context_window=4096, max_output=4092,
        )

    # ── Découverte ─────────────────────────────────────────

    def list_available_providers(self) -> List[Dict[str, Any]]:
        """Liste les providers disponibles (ceux avec clé ou locaux)."""
        eps = _load_provider_endpoints(self.cat)
        result = []
        for ref, info in eps.items():
            if info.get("api_key") or _resolve_api_keys(ref):
                result.append({"ref": ref, "name": ref, "status": "available"})
        # Providers locaux sans clé
        for ref in ("ollama",):
            try:
                urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
                result.append({"ref": ref, "name": ref, "status": "available"})
            except Exception:
                pass
        return result

    def list_available_models(self, provider_ref: str) -> List[Dict[str, Any]]:
        """Retourne les modèles disponibles pour un provider.

        Priorité :
        1. Découverte via l'API du provider (``GET /v1/models``)
        2. Fallback base de données
        """
        # Essayer la découverte API en premier
        api_models = self._discover_models_from_api(provider_ref)
        if api_models:
            return api_models

        # Fallback : base de données
        models = []
        if self.cat:
            try:
                rows = self.cat.conn.execute("""
                    SELECT m.ref, m.name, kem.available
                    FROM catalogue_models m
                    JOIN provider_models_mapping kem ON kem.model_id = m.id
                    JOIN catalogue_providers p ON p.id = kem.provider_id
                    WHERE p.ref = ?
                """, (provider_ref,)).fetchall()
                for r in rows:
                    models.append({
                        "ref": r["ref"],
                        "name": r.get("name") or r["ref"],
                        "available": bool(r.get("available", 1)),
                    })
            except Exception:
                pass
        return models

    def _discover_models_from_api(self, provider_ref: str) -> List[Dict[str, Any]]:
        """Interroge le listing de modèles du provider pour découvrir les modèles.

        Gère les formats OpenAI-compatibles (``{"data": [{"id": ...}]}`` :
        nvidia, groq, openrouter, together, deepinfra, ollama…) ainsi que le
        format Cohere (``{"models": [{"name": ...}]}``).

        Google (api_type=gemini) utilise ``GET /v1beta/models`` avec la clé
        en header ``x-goog-api-key`` et un format de réponse différent.
        """
        ep = self._get_endpoint(provider_ref)
        base_url = ep.get("base_url", "")
        if not base_url:
            return []
        api_type = ep.get("api_type") or "openai"
        api_key = ep.get("api_key", "")

        if api_type == "gemini":
            return self._discover_gemini_models(base_url, api_key)

        # Chemins candidats : endpoint DB ou défaut peut être /v1 (openai),
        # /v1beta (google), / (cohere), ou nu (huggingface). On essaie
        # successivement les listings courants.
        candidates = []
        stripped = base_url.rstrip("/")
        if stripped.endswith("/v1"):
            candidates = [f"{stripped}/models", f"{stripped.replace('/v1', '')}/models"]
        elif stripped.endswith("/v1beta"):
            candidates = [f"{stripped}/models"]
        else:
            candidates = [f"{stripped}/models", f"{stripped}/v1/models"]
        # Ollama-native listing endpoint (local & cloud)
        if "ollama" in provider_ref:
            ollama_base = stripped
            if ollama_base.endswith("/v1"):
                ollama_base = ollama_base[:-3]
            elif ollama_base.endswith("/v1beta"):
                ollama_base = ollama_base[:-7]
            candidates.append(f"{ollama_base}/api/tags")

        data = None
        last_err = None
        for models_url in candidates:
            headers = {
                "Authorization": f"Bearer {api_key}" if api_key else "",
                "User-Agent": _USER_AGENT,
            }
            req = urllib.request.Request(models_url, headers={k: v for k, v in headers.items() if v})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                    break
            except Exception as e:
                last_err = e
                continue
        if data is None:
            return []

        result = []
        # Format OpenAI : data[].id — Cohere : models[].name
        raw = data.get("data") or data.get("models") or []
        for m in raw:
            mid = m.get("id") or m.get("name") or m.get("model") or ""
            if not mid:
                continue
            if mid.startswith("models/"):
                mid = mid[len("models/"):]
            # Filtrer les modèles non-texte (embeddings, image, etc.)
            if any(x in mid.lower() for x in ("embed", "whisper", "tts",
                                              "dall-e", "guard", "image")):
                continue
            result.append({
                "ref": mid,
                "name": m.get("owned_by") or mid,
                "available": True,
            })
        return result

    def _discover_gemini_models(self, base_url: str,
                                api_key: str) -> List[Dict[str, Any]]:
        """Découverte des modèles via l'API Gemini (v1beta/models).

        Le nom complet est ``models/gemini-2.5-flash`` — on normalise en
        ``gemini-2.5-flash`` pour rester cohérent avec le catalogue.
        """
        models_url = base_url.rstrip("/") + "/models"
        headers = {
            "x-goog-api-key": api_key,
            "User-Agent": _USER_AGENT,
        }
        req = urllib.request.Request(models_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            return []

        result = []
        for m in data.get("models", []):
            name = m.get("name", "")
            mid = name[len("models/"):] if name.startswith("models/") else name
            if not mid:
                continue
            # Filtrer les modèles non-texte
            if any(x in mid.lower() for x in ("embed", "whisper", "tts",
                                              "imagen", "veo", "lyria",
                                              "robotics", "aqa")):
                continue
            result.append({
                "ref": mid,
                "name": m.get("displayName") or mid,
                "available": True,
            })
        return result

    # ── Probe (disponibilité réelle des modèles) ─────────────

    # Test agentic RÉALISTE : une question qui nécessite un outil.
    # Le modèle doit comprendre qu'il faut appeler l'outil `bash` AVEC une
    # commande cohérente (ex. `df -h` pour l'espace disque). On vérifie :
    #   - l'appel d'outil est bien produit (tool_calls),
    #   - l'argument `command` est cohérent avec la question (mots-clés).
    # IMPORTANT : le nom de l'outil doit être NATUREL (`bash`, comme opencode)
    # — un nom artificiel (fake_shell_v1) n'est pas reconnu par les modèles
    # qui répondent alors en texte (faux « non-agentic »).
    PROBE_TOOLS = [{
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command on this system and return its output. Use for terminal operations like df, free, ls, git, etc.",
            "parameters": {"type": "object",
                           "properties": {
                               "command": {"type": "string",
                                           "description": "The bash command to execute, e.g. df -h"},
                               "timeout": {"type": "integer",
                                           "description": "Optional timeout in milliseconds"},
                           },
                           "required": ["command"]},
        },
    }]
    PROBE_MSG = [{"role": "user",
                  "content": "Combien d'espace libre reste-t-il sur mon disque dur ?"}]
    # Mots-clés attendus dans la commande pour valider la cohérence agentic.
    PROBE_KEYWORDS = ("df", "disk", "space", "storage", "filesystem",
                      "free", "usage", "mount", "fs")

    def _probe_command_coherent(self, command: str) -> bool:
        """Vrai si la commande demandée est cohérente avec la question disque."""
        low = (command or "").lower()
        return any(k in low for k in self.PROBE_KEYWORDS)

    def _probe_one(self, provider_ref: str, model_ref: str,
                   timeout: float = 12.0) -> Dict[str, Any]:
        """Probe UN modèle : succès, latence, capacité agentic réelle.

        `timeout` = timeout réseau pour CETTE requête (pas un timeout global).
        Retourne {ok, latency_ms, tool_calls, coherent, error?, error_code?}.
        `ok` = le modèle a appelé fake_shell_v1 avec une commande COHÉRENTE.
        Un modèle qui répond en texte n'est PAS compté ok (non-agentic).
        """
        import threading
        import time as _t
        result: Dict[str, Any] = {}
        t0 = _t.time()

        def _run():
            try:
                resp = self.chat(
                    provider_ref=provider_ref, model_ref=model_ref,
                    messages=self.PROBE_MSG, tools=self.PROBE_TOOLS,
                    temperature=0.0, max_tokens=100, stream=False)
                tc = getattr(resp, "tool_calls", None) or []
                result["tool_calls"] = bool(tc)
                coherent = False
                cmd = ""
                if tc:
                    try:
                        args = json.loads(tc[0]["function"].get("arguments", "{}"))
                        cmd = str(args.get("command") or "")
                    except Exception:
                        cmd = ""
                    coherent = self._probe_command_coherent(cmd)
                result["coherent"] = coherent
                result["command"] = cmd[:80]
                result["ok"] = coherent
                result["latency_ms"] = int((_t.time() - t0) * 1000)
            except Exception as e:
                result["ok"] = False
                result["latency_ms"] = int((_t.time() - t0) * 1000)
                result["error"] = str(e)[:300]

        th = threading.Thread(target=_run, daemon=True)
        th.start()
        th.join(timeout)
        if th.is_alive():
            result["ok"] = False
            result["timeout"] = True
            result["error"] = f"timeout après {timeout}s"
        if "error_code" not in result and result.get("error"):
            result["error_code"] = self._probe_classify(result["error"])
        return result

    # ── Test agentic actif (séquence de prompts) ──────────────────────
    # Déclenché quand un codeur/merger n'a produit AUCUN tool_call dans sa
    # session (improbable pour un rôle qui doit écrire du code). On vérifie
    # que le modèle est bien capable d'appeler des outils, avec UNE SÉRIE de
    # prompts conçus pour nécessiter un tool_call (plus fiable qu'un seul).
    # Les erreurs NON agentic (rate_limit, quota, auth, timeout, réseau)
    # sont IGNORÉES : elles ne comptent ni pour ni contre (le modèle peut
    # être parfaitement agentic mais simplement en quota).

    # Série de prompts → tous pointent vers un outil nécessaire.
    AGENTIC_TEST_CASES = [
        {
            "name": "disk",
            "messages": [{"role": "user",
                          "content": "Quelle est l'espace libre restant sur le disque ?"}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "fake_shell_v1",
                    "description": "Exécute une commande shell sur la machine.",
                    "parameters": {"type": "object",
                                   "properties": {"command": {"type": "string"}},
                                   "required": ["command"]},
                },
            }],
            "coherent": ("df", "disk", "space", "storage", "filesystem", "free"),
        },
        {
            "name": "write",
            "messages": [{"role": "user",
                          "content": "Crée un fichier nommé test_agentic.txt contenant 'ok'."}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "write_file_v1",
                    "description": "Écrit un fichier sur le disque.",
                    "parameters": {"type": "object",
                                   "properties": {"path": {"type": "string"},
                                                  "content": {"type": "string"}},
                                   "required": ["path", "content"]},
                },
            }],
            "coherent": ("test_agentic",),
        },
        {
            "name": "list",
            "messages": [{"role": "user",
                          "content": "Liste les fichiers du répertoire courant."}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "list_dir_v1",
                    "description": "Liste le contenu d'un répertoire.",
                    "parameters": {"type": "object",
                                   "properties": {"path": {"type": "string"}},
                                   "required": ["path"]},
                },
            }],
            "coherent": (),
        },
        {
            "name": "search",
            "messages": [{"role": "user",
                          "content": "Recherche 'TODO' dans les fichiers Python du projet."}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "grep_v1",
                    "description": "Recherche une chaîne dans des fichiers.",
                    "parameters": {"type": "object",
                                   "properties": {"pattern": {"type": "string"},
                                                  "path": {"type": "string"}},
                                   "required": ["pattern"]},
                },
            }],
            "coherent": (),
        },
        {
            "name": "compute",
            "messages": [{"role": "user",
                          "content": "Calcule 15 * 7 et écris le résultat dans un fichier."}],
            "tools": [{
                "type": "function",
                "function": {
                    "name": "write_file_v1",
                    "description": "Écrit un fichier sur le disque.",
                    "parameters": {"type": "object",
                                   "properties": {"path": {"type": "string"},
                                                  "content": {"type": "string"}},
                                   "required": ["path", "content"]},
                },
            }],
            "coherent": ("result",),
        },
    ]

    def test_agentic(self, provider_ref: str, model_ref: str,
                     timeout: float = 15.0,
                     max_cases: int = 5) -> Dict[str, Any]:
        """Teste si un modèle appelle réellement des outils, sur N cas.

        Chaque cas est un prompt conçu pour nécessiter un tool_call. Le modèle
        est agentic s'il appelle un outil (et, quand vérifiable, cohérent avec
        la demande) sur AU MOINS UN cas.

        Les erreurs NON agentic (rate_limit/quota/auth/timeout/réseau) sont
        ignorées : elles ne font pas échouer le test (le modèle peut être
        agentic mais en quota). Seul « répond en texte sans tool_call » ou
        « tool unsupported » compte comme échec.

        Retourne {ok, tool_calls, coherent, tested, skipped_errors, error?}.
        """
        import time as _t
        results = []
        tool_calls_total = 0
        coherent_total = 0
        skipped = 0
        last_err = ""
        cases = self.AGENTIC_TEST_CASES[:max_cases]
        t0 = _t.time()

        for case in cases:
            try:
                resp = self.chat(
                    provider_ref=provider_ref, model_ref=model_ref,
                    messages=case["messages"], tools=case["tools"],
                    temperature=0.0, max_tokens=80, stream=False,
                    caller_id="test:agentic")
                tc = getattr(resp, "tool_calls", None) or []
                if tc:
                    tool_calls_total += 1
                    args = tc[0]["function"].get("arguments", "") if tc else ""
                    low = (args or "").lower()
                    coh = any(k in low for k in case["coherent"]) \
                        if case["coherent"] else True
                    if coh:
                        coherent_total += 1
                    results.append({"case": case["name"], "tool_call": True,
                                    "coherent": coh})
                else:
                    # Réponse texte SANS tool_call malgré des outils fournis :
                    # échec agentic (le modèle n'a pas saisi l'opportunité).
                    results.append({"case": case["name"], "tool_call": False,
                                    "coherent": False})
            except Exception as e:
                err_str = str(e)
                cat = self._probe_classify(err_str)
                if cat in ("rate_limit", "quota", "auth", "credit",
                           "timeout", "not_found"):
                    # Erreur NON agentic : ignorée (ni pour ni contre).
                    skipped += 1
                    last_err = err_str[:200]
                    results.append({"case": case["name"], "skipped": True,
                                    "error_code": cat})
                else:
                    # Erreur potentiellement liée au tool calling
                    # (tool_format, unknown…) : comptée comme échec doux.
                    results.append({"case": case["name"], "tool_call": False,
                                    "coherent": False, "error_code": cat})
                    last_err = err_str[:200]

        ok = coherent_total >= 1
        return {
            "ok": ok,
            "tool_calls": tool_calls_total,
            "coherent": coherent_total,
            "tested": len(cases) - skipped,
            "skipped_errors": skipped,
            "cases": results,
            "latency_ms": int((_t.time() - t0) * 1000),
            "last_error": last_err,
        }

    @staticmethod
    def _probe_classify(err: str) -> str:
        low = (err or "").lower()
        for kw in ("auth", "401", "invalid api key", "api key", "unauthorized",
                   "permission denied"):
            if kw in low:
                return "auth"
        # Modèle ACTIF mais appel d'outil refusé pour une raison de FORMAT
        # (ex. nvidia : « only supports single tool-calls at once »). On ne le
        # marque PAS indisponible : il répond, on ne sait juste pas s'il est
        # agentic. cf. point D.
        if "single tool-call" in low or "single tool call" in low \
                or "only supports single" in low:
            return "tool_format"
        # Upstream qui casse le tool calling sur certains modèles raisonneurs
        # (opencode-zen/deepseek : « reasoning_content … must be passed back »).
        # Modèle actif, agentic indéterminé → pas d'indispo. cf. point D.
        if "reasoning_content" in low and ("passed back" in low
                                           or "thinking mode" in low):
            return "reasoning_format"
        for kw in ("insufficient credits", "never purchased", "billing",
                   "credit"):
            if kw in low:
                return "credit"
        for kw in ("404", "not found", "not exist", "does not exist",
                   "no model", "unknown model"):
            if "404" in low or "not found" in low or "not exist" in low:
                return "not_found"
        for kw in ("timeout", "timed out", "took too long"):
            if kw in low:
                return "timeout"
        for kw in ("rate limit", "429", "quota", "per-day", "rpm"):
            if kw in low:
                return "rate_limit"
        return "unknown"

    def _probe_is_api_fail(self, code: str) -> bool:
        """Un échec API DÉFINITIF (auth/credit/not_found) = modèle indisponible.

        On ne périme PAS sur les erreurs transitoires ou « modèle actif mais
        agentic indéterminé » :
          - rate_limit / unknown : transient (hammering, réseau) → ne périme pas
          - timeout           : modèle LENT, pas bloqué (cf. point E)
          - tool_format / reasoning_format : modèle actif, agentic indéterminé
            (cf. point D)
        """
        return code in ("auth", "credit", "not_found")

    # Espacement entre probes d'un même provider (secondes). Les providers
    # gratuits / à RPM bas (opencode-zen, google…) ont besoin de plus d'air
    # pour ne pas se prendre des rate-limit qui fausseraient la probe.
    # cf. point A.
    _PROBE_INTRA_SPACING = 1.0
    _PROBE_EXTRA_SPACING = {
        "opencode-zen": 2.5, "google": 1.5, "llm7": 1.5,
        "kilo": 1.2, "groq": 1.2, "huggingface": 1.0,
    }

    def _probe_spacing(self, provider_ref: str) -> float:
        return DirectBridge._PROBE_EXTRA_SPACING.get(
            provider_ref, DirectBridge._PROBE_INTRA_SPACING)

    def probe(self, provider_ref: Optional[str] = None,
              model_ref: Optional[str] = None,
              timeout: float = 12.0) -> Dict[str, Any]:
        """Probe un modèle, tous les modèles d'un provider, ou tous.

        - probe()                     → tous les modèles de tous les providers
        - probe(provider)             → tous les modèles du provider
        - probe(provider, model)      → un seul modèle

        PARALLÉLISME PAR PROVIDER : un thread par provider, chaque provider
        probe ses modèles SÉQUENTIELLEMENT. Paralléliser par MODÈLE (20 requêtes
        simultanées vers le même provider) sature son quota/rate-limit
        (ex. opencode-zen, google à RPM bas) → faux unavailable. Un thread par
        fournisseur reste dans la limite ~20 threads simultanés.

        Marque `unavailable` les modèles en échec API DÉFINITIF (auth/credit/
        not_found), `slow` les modèles lents (timeout), et laisse actifs les
        modèles en erreur transitoire (rate_limit/unknown) ou à agentic
        indéterminé (tool_format/reasoning_format).
        """
        import threading
        # Cible : {provider: [modèles]} — pour un seul modèle, (provider,[model]).
        by_prov: Dict[str, List[str]] = {}
        if provider_ref and model_ref:
            by_prov[provider_ref] = [model_ref]
        else:
            for r in self._probe_candidates(provider_ref):
                by_prov.setdefault(r["prov"], []).append(r["pname"])

        results: List[Dict] = []
        lock = threading.Lock()

        def _probe_provider(prov, models):
            """Probe séquentiellement les modèles d'un provider."""
            spacing = self._probe_spacing(prov)
            for mname in models:
                r = self._probe_one(prov, mname, timeout)
                r["provider"] = prov
                r["model"] = mname
                code = r.get("error_code", "unknown")
                if r["ok"]:
                    # Répond + tool cohérent → disponible, agentic, ni lent ni périmé.
                    self._probe_set_available(prov, mname, True, agentic=True,
                                              slow=False, deprecated=False)
                elif code in ("auth", "credit", "not_found"):
                    # Échec DÉFINITIF → indisponible. not_found = modèle
                    # périmé/disparu (colonne deprecated). cf. point B.
                    self._probe_set_available(prov, mname, False, reason=code,
                                              agentic=False,
                                              deprecated=(code == "not_found"))
                elif code == "timeout":
                    # Lent mais JOIGNABLE → marqué `slow`, PAS `unavailable`.
                    # cf. point E (séparer lent / bloqué).
                    self._probe_set_available(prov, mname, True, agentic=None,
                                              slow=True, deprecated=False)
                else:
                    # rate_limit / unknown / tool_format / reasoning_format :
                    # modèle actif (ou transient) → ni indispo ni périmé.
                    # (tool_format/reasoning_format = actif mais agentic
                    #  indéterminé ; cf. point D)
                    pass
                with lock:
                    results.append(r)
                # Espacement intra-provider (configurable par provider) : évite
                # le rate-limit (RPM) quand on enchaîne les probes. cf. point A.
                time.sleep(spacing)

        threads = [threading.Thread(target=_probe_provider,
                                    args=(prov, models), daemon=True)
                   for prov, models in by_prov.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ok = sum(1 for r in results if r["ok"])                       # agentic (tool cohérent)
        non_agentic = sum(1 for r in results if not r["ok"]
                          and not r.get("error_code"))                # répond mais non-agentic
        fail = sum(1 for r in results if not r["ok"]
                   and r.get("error_code"))                           # échec API
        return {
            "status": "ok", "probed": len(results),
            "ok_agentic": ok, "non_agentic": non_agentic, "unavailable": fail,
            "results": results,
        }

    def _probe_candidates(self, provider_ref: Optional[str] = None):
        """Liste des modèles à prober : (provider, provider_model_name).

        NE filtre PAS sur available (on probe aussi les modèles marqués
        unavailable, pour les réévaluer) — on trie simplement par score
        benchmark décroissant (les modèles sans score vont en dernier).
        model_efficacy.model_ref = model_key canonique → jointure directe.
        """
        rows = []
        if not self.cat:
            return rows
        try:
            if provider_ref:
                rows = self.cat.conn.execute("""
                    SELECT p.ref AS prov, kem.provider_model_name AS pname
                    FROM provider_models_mapping kem
                    JOIN catalogue_providers p ON p.id = kem.provider_id
                    JOIN catalogue_models cm ON cm.id = kem.model_id
                    LEFT JOIN model_efficacy me ON me.model_ref = cm.model_key
                        AND me.use_case = 'general'
                    WHERE p.ref = ?
                    ORDER BY COALESCE(me.global_score, -1) DESC, kem.provider_model_name
                """, (provider_ref,)).fetchall()
            else:
                rows = self.cat.conn.execute("""
                    SELECT p.ref AS prov, kem.provider_model_name AS pname
                    FROM provider_models_mapping kem
                    JOIN catalogue_providers p ON p.id = kem.provider_id
                    JOIN catalogue_models cm ON cm.id = kem.model_id
                    LEFT JOIN model_efficacy me ON me.model_ref = cm.model_key
                        AND me.use_case = 'general'
                    ORDER BY p.ref, COALESCE(me.global_score, -1) DESC,
                             kem.provider_model_name
                """).fetchall()
        except Exception:
            pass
        return [dict(r) for r in rows]

    def _probe_set_available(self, provider_ref: str, model_name: str,
                             available: bool, reason: str = "",
                             agentic: Optional[bool] = None,
                             slow: Optional[bool] = None,
                             deprecated: Optional[bool] = None) -> None:
        """Marque un modèle disponible/indisponible en base (+ agentic/slow/deprecated).

        - available=True  : mapping.available=1, provider_models.unavailable=0
        - available=False : mapping.available=0, provider_models.unavailable=1
        - agentic         : force/efface le flag agentic (None = ne touche pas)
        - slow            : modèle lent mais joignable (None = ne touche pas)
        - deprecated      : modèle périmé/disparu (None = ne touche pas)
        """
        if not self.cat:
            return
        try:
            now = int(time.time())
            if available:
                self.cat.conn.execute("""
                    UPDATE provider_models SET unavailable = 0, noretryuntil = 0
                    WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                      AND provider_model_name = ?
                """, (provider_ref, model_name))
                self.cat.conn.execute("""
                    UPDATE provider_models_mapping SET available = 1, last_error = NULL
                    WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                      AND provider_model_name = ?
                """, (provider_ref, model_name))
            else:
                self.cat.conn.execute("""
                    UPDATE provider_models SET unavailable = 1, noretryuntil = ?
                    WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                      AND provider_model_name = ?
                """, (now + 3600, provider_ref, model_name))
                self.cat.conn.execute("""
                    UPDATE provider_models_mapping SET available = 0, last_error = ?
                    WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                      AND provider_model_name = ?
                """, (reason[:200], provider_ref, model_name))
            if agentic is not None:
                self.cat.conn.execute("""
                    UPDATE provider_models SET agentic = ?
                    WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                      AND provider_model_name = ?
                """, (1 if agentic else 0, provider_ref, model_name))
            if slow is not None:
                self.cat.conn.execute("""
                    UPDATE provider_models SET slow = ?
                    WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                      AND provider_model_name = ?
                """, (1 if slow else 0, provider_ref, model_name))
            if deprecated is not None:
                self.cat.conn.execute("""
                    UPDATE provider_models SET deprecated = ?
                    WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                      AND provider_model_name = ?
                """, (1 if deprecated else 0, provider_ref, model_name))
            self.cat.conn.commit()
        except Exception:
            try:
                self.cat.conn.rollback()
            except Exception:
                pass

    # ── Santé ──────────────────────────────────────────────

    def health_check(self, provider_ref: Optional[str] = None) -> Dict[str, Any]:
        """Vérifie qu'un provider est joignable."""
        if provider_ref:
            ep = self._get_endpoint(provider_ref)
            base = ep.get("base_url", "")
            if not base:
                return {"status": "error", "error": "no endpoint"}
            try:
                urllib.request.urlopen(base, timeout=5)
                return {"status": "ok", "endpoint": base}
            except Exception as e:
                return {"status": "error", "error": str(e)[:100]}
        return {"status": "ok", "bridge": "direct"}

    # ── Classification ─────────────────────────────────────

    def classify_error(self, error: Any,
                       provider_ref: str = "",
                       model_ref: str = "") -> BridgeError:
        """Délègue à la fonction de classification."""
        if isinstance(error, BridgeError):
            return error
        return _classify_exception(error, provider_ref, model_ref)
