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

from modules.llm_manager.base_bridge import (
    BaseBridge, BridgeError, ErrorCategory,
    ChatResponse, ModelCapabilities,
)


_USER_AGENT = "ModelWeaver/1.0 (+https://github.com/ModelWeaver)"

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
            LEFT JOIN key_endpoint_models kem ON kem.provider_id = p.id
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

    return result


_DEFAULT_ENDPOINTS = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "ollama": "http://localhost:11434/v1",
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
}


def _resolve_api_keys(provider_ref: str, key_refs: Optional[set] = None) -> List[str]:
    """Résout les clés API pour un provider : env vars, .env, puis key_refs."""
    keys = []

    # Charger .env si présent (pour les providers sans env var exportée)
    _load_dotenv_once()

    for env_var in _ENV_KEY_MAP.get(provider_ref, []):
        val = os.environ.get(env_var)
        if val:
            keys.append(val)

    if key_refs:
        from modules.key_manager.key_manager import KeyManager
        from modules.sql.db import ModelWeaverDB
        try:
            km = KeyManager(ModelWeaverDB())
            for ref in key_refs:
                val = km.get_key(ref)
                if val and val not in keys:
                    keys.append(val)
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


def _build_tools_param(tools: Optional[List[Dict]] = None) -> Optional[List[dict]]:
    """Formate les outils au format OpenAI function calling."""
    if not tools:
        return None
    return tools  # déjà en format OpenAI


def _build_model_id(provider_ref: str, model_ref: str) -> str:
    """Construit l'ID modèle pour l'API du provider.

    La plupart des providers OpenAI-compatibles acceptent juste le model_ref
    tel quel (sans préfixe provider/).
    """
    if "/" in model_ref and not model_ref.startswith(f"{provider_ref}/"):
        return model_ref  # déjà un nom complet (ex: meta/llama-3.3-70b)
    return model_ref


# ── Classification des erreurs HTTP ────────────────────────

def _classify_http_error(status: int, body: str,
                         provider_ref: str, model_ref: str) -> BridgeError:
    """Classe une erreur HTTP en BridgeError."""
    msg = body[:300] if body else f"HTTP {status}"

    if status == 401 or status == 403:
        return BridgeError(ErrorCategory.AUTH, msg, provider_ref, model_ref)

    if status == 429:
        retry_after = None
        # Vérifier si c'est un vrai rate-limit ou un quota épuisé
        if "insufficient_quota" in body.lower() or "quota" in body.lower():
            return BridgeError(ErrorCategory.AUTH, msg, provider_ref, model_ref)
        return BridgeError(ErrorCategory.RATE_LIMIT, msg, provider_ref, model_ref,
                          retry_after_seconds=retry_after)

    if status == 413 or status == 400 and "context" in body.lower():
        return BridgeError(ErrorCategory.CONTEXT, msg, provider_ref, model_ref)

    if status == 408 or status == 504:
        return BridgeError(ErrorCategory.TIMEOUT, msg, provider_ref, model_ref)

    if status >= 500:
        return BridgeError(ErrorCategory.SERVER, msg, provider_ref, model_ref)

    return BridgeError(ErrorCategory.UNKNOWN, msg, provider_ref, model_ref)


def _classify_exception(exc: Exception,
                        provider_ref: str, model_ref: str) -> BridgeError:
    """Classe une exception réseau en BridgeError."""
    msg = str(exc)[:300]

    if isinstance(exc, urllib.error.HTTPError):
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return _classify_http_error(exc.code, body, provider_ref, model_ref)

    if isinstance(exc, urllib.error.URLError):
        return BridgeError(ErrorCategory.SERVER, f"Connection failed: {msg}",
                          provider_ref, model_ref)

    if isinstance(exc, TimeoutError):
        return BridgeError(ErrorCategory.TIMEOUT, msg, provider_ref, model_ref)

    return BridgeError(ErrorCategory.UNKNOWN, msg, provider_ref, model_ref)


# ── Bridge principal ────────────────────────────────────────

class DirectBridge(BaseBridge):
    """Pont LLM direct via API OpenAI-compatible.

    Remplace LiteLLMBridge. Tous les providers OpenAI-compatibles sont
    supportés via la même implémentation. Les providers au format différent
    (Google, Anthropic) tombent sur leurs bridges natifs existants.
    """

    def __init__(self, cat=None, km=None):
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

    # ── Chat ───────────────────────────────────────────────

    def chat(self, provider_ref: str, model_ref: str,
             messages: List[Dict[str, str]],
             temperature: float = 0.7,
             max_tokens: Optional[int] = None,
             system_prompt: Optional[str] = None,
             stream: bool = False,
             **params) -> ChatResponse:
        """Appelle l'API du provider et retourne la réponse."""
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

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            raise _classify_exception(exc, provider_ref, model_ref)

        return self._parse_response(data, provider_ref, model_ref)

    def _google_chat(self, ep: dict, provider_ref: str, model_ref: str,
                     messages: List[Dict[str, str]],
                     temperature: float, max_tokens: Optional[int],
                     system_prompt: Optional[str] = None,
                     **params) -> ChatResponse:
        """Appelle l'API Google Gemini (format différent d'OpenAI)."""
        base = ep.get("base_url", "https://generativelanguage.googleapis.com/v1beta")
        api_key = ep.get("api_key") or ""
        url = f"{base.rstrip('/')}/models/{model_ref}:generateContent"

        # Convertir les messages au format Gemini
        contents = []
        system_instruction = None
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role == "system":
                system_instruction = {"parts": [{"text": content}]}
                continue
            if role == "assistant":
                gemini_role = "model"
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
            gemini_tools = []
            for t in tools:
                if t.get("type") == "function":
                    fn = t.get("function", {})
                    gemini_tools.append({
                        "functionDeclarations": [{
                            "name": fn.get("name", ""),
                            "description": fn.get("description", ""),
                            "parameters": fn.get("parameters", {}),
                        }]
                    })
            body["tools"] = gemini_tools

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

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            raise _classify_exception(exc, provider_ref, model_ref)

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
                    "id": fc.get("name", "fc_0"),
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {})),
                    },
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
        raise NotImplementedError("stream à implémenter")

    def _chat_stream_internal(self, *args, **kwargs) -> ChatResponse:
        raise NotImplementedError("stream à implémenter")

    # ── Parsing réponse ────────────────────────────────────

    @staticmethod
    def _parse_response(data: dict, provider_ref: str,
                        model_ref: str) -> ChatResponse:
        """Parse une réponse JSON au format OpenAI en ChatResponse."""
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {})

        content = message.get("content") or ""
        tool_calls_raw = message.get("tool_calls")

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
        """Retourne les capacités depuis la base ou valeurs par défaut."""
        if self.cat:
            try:
                row = self.cat.conn.execute("""
                    SELECT mc.* FROM model_capabilities mc
                    WHERE mc.model_ref = ?
                """, (model_ref,)).fetchone()
                if row:
                    return ModelCapabilities(
                        context_window=row.get("max_context_tokens", 4096),
                        max_output=row.get("max_output_tokens", 4096),
                        supports_function_calling=bool(row.get("supports_function_calling", 1)),
                        supports_vision=bool(row.get("supports_vision", 0)),
                    )
            except Exception:
                pass
        return ModelCapabilities(context_window=4096, max_output=4096,
                                 supports_function_calling=True,  # la plupart des modèles récents supportent
                                 supports_vision=False)

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
                    JOIN key_endpoint_models kem ON kem.model_id = m.id
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
        """Interroge ``GET /v1/models`` du provider pour découvrir les modèles.

        Fonctionne avec tous les providers OpenAI-compatibles (nvidia, groq,
        openrouter, together, deepinfra, ollama…) ainsi que les providers
        avec endpoint personnalisé.
        """
        ep = self._get_endpoint(provider_ref)
        base_url = ep.get("base_url", "")
        if not base_url:
            return []

        models_url = base_url.rstrip("/") + "/models"
        headers = {
            "Authorization": f"Bearer {ep.get('api_key', '')}" if ep.get('api_key') else "",
            "User-Agent": _USER_AGENT,
        }

        req = urllib.request.Request(models_url, headers={k: v for k, v in headers.items() if v})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            return []

        result = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            if not mid:
                continue
            # Filtrer les modèles non-texte (embeddings, image, etc.)
            if any(x in mid.lower() for x in ("embed", "whisper", "tts", "dall-e", "guard")):
                continue
            result.append({
                "ref": mid,
                "name": m.get("owned_by") or mid,
                "available": True,
            })
        return result

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
