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

# ── Repos après échec d'un appel LLM (rate-limit / erreur) ──────────
# Quand un appel échoue, on marque le modèle « unavailable » et on ne le
# retente pas avant noretryuntil. La durée d'interdiction croît à chaque
# échec consécutif : notrytime = TIME_NO_RESTART_INIT × multiply^n.
TIME_NO_RESTART_INIT = 5.0          # durée initiale de repos (secondes)
TIME_NO_RESTART_MULTIPLY = 1.5      # croissance ×1.5 à chaque échec consécutif
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

    # Providers ayant une clé API (modelweaver.db/api_keys) mais aucune ligne
    # key_endpoint_models : on ajoute leur endpoint par défaut pour qu'ils
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


def _build_tools_param(tools: Optional[List[Dict]] = None) -> Optional[List[dict]]:
    """Formate les outils au format OpenAI function calling."""
    if not tools:
        return None
    return tools  # déjà en format OpenAI


def _build_model_id(provider_ref: str, model_ref: str) -> str:
    """Construit l'ID modèle pour l'API du provider.

    La plupart des providers OpenAI-compatibles acceptent le model_ref tel
    quel. Si la ref est préfixée par le provider lui-même (ex. openrouter/
    openai/gpt-5.2-chat pour provider openrouter), on retire le préfixe
    redondant pour envoyer openai/gpt-5.2-chat.
    """
    if model_ref.startswith(f"{provider_ref}/"):
        return model_ref[len(provider_ref) + 1:]
    return model_ref


# ── Classification des erreurs HTTP ────────────────────────

def _detect_limit_type(body: str, status: int, headers=None) -> Optional[str]:
    """Détecte le type de limite depuis le body / en-têtes d'erreur."""
    bl = (body or "").lower()
    if status == 429:
        # Quota journalier (ex. openrouter free-models-per-day) : ne se résout
        # pas en secondes → doit poser un repos LONG (24h), pas un backoff court.
        if "per-day" in bl or "daily" in bl or "per day" in bl or "day limit" in bl:
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
            if duration is None:
                row = self.cat.conn.execute("""
                    SELECT notrytime FROM provider_models
                    WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                      AND provider_model_name = ?
                """, (provider_ref, model_ref)).fetchone()
                last = row["notrytime"] if row else 0.0
                duration = TIME_NO_RESTART_INIT if not last else last * TIME_NO_RESTART_MULTIPLY
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
                  AND provider_model_name = ?
            """, (now + duration, duration, provider_ref, model_ref))
            # Problème de CRÉDIT (402 "insufficient credits") : c'est un souci
            # de COMPTE = provider-wide — tous les modèles du provider sont
            # morts (ex. openrouter sans crédits). On pose un repos modéré
            # (1h) sur TOUT le provider pour que le fallback n'enchaîne pas
            # ses centaines de modèles morts un par un, sans pour autant le
            # bloquer la journée entière. NE PAS déclencher sur un simple
            # quota transitoire (429) : un quota google ponctuel ne signifie
            # pas que tout google est mort.
            if provider_wide:
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
            self.cat.conn.execute("""
                UPDATE provider_models
                SET unavailable = 0, noretryuntil = 0, notrytime = 0
                WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                  AND provider_model_name = ?
            """, (provider_ref, model_ref))
            try:
                self.cat.conn.commit()
            except Exception:
                pass
        except Exception:
            pass

    def _log_call(self, provider_ref: str, model_ref: str, success: bool,
                  latency_ms: float, usage: Optional[dict] = None,
                  error_code: str = "", tokens_thinking: int = 0,
                  agent_id: Optional[str] = None) -> None:
        """Journalise un appel LLM réel dans model_call_log (métriques runtime).

        Référencé par ID (provider_id/model_id/provider_model_id), pas par nom.
        ``agent_id`` identifie l'agent appelant (None pour probes/health/sync).
        Utilisé par le scoring d'allocation (taux de succès, latence, tokens)
        et les analyses de consommation par agent. Best-effort : ne lève jamais.
        """
        if not self.cat:
            return
        try:
            u = usage or {}
            self.cat.conn.execute("""
                INSERT INTO model_call_log
                    (provider_id, model_id, provider_model_id, agent_id, success,
                     tokens_in, tokens_out, tokens_thinking, latency_ms, error_code)
                VALUES (
                    COALESCE((SELECT id FROM catalogue_providers WHERE ref = ?), 0),
                    COALESCE((SELECT id FROM catalogue_models WHERE ref = ?), 0),
                    (SELECT pm.id FROM provider_models pm
                      JOIN catalogue_providers p ON p.id = pm.provider_id
                     WHERE p.ref = ? AND pm.provider_model_name = ?),
                    ?, ?, ?, ?, ?, ?, ?)
            """, (provider_ref, model_ref, provider_ref, model_ref,
                  (str(agent_id)[:80] if agent_id else None),
                  int(success), int(u.get("prompt_tokens") or 0),
                  int(u.get("completion_tokens") or 0), int(tokens_thinking or 0),
                  float(latency_ms or 0), error_code[:100]))
            self.cat.conn.commit()
            # Fenêtre bornée : purge au-delà de 10k lignes (garder les plus
            # récentes). Tri par created_at (chronologique) — plus sûr que id
            # si on change de backend / si des ids sont réutilisés.
            try:
                self.cat.conn.execute("""
                    DELETE FROM model_call_log WHERE id NOT IN (
                        SELECT id FROM model_call_log
                        ORDER BY created_at DESC, id DESC LIMIT 10000)
                """)
                self.cat.conn.commit()
            except Exception:
                pass
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
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read().decode())
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

        _t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
            self._log_call(provider_ref, model_ref, True,
                           (time.time() - _t0) * 1000.0,
                           usage=(data or {}).get("usage"),
                           agent_id=params.get("agent_id"))
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
                self._log_call(provider_ref, model_ref, False,
                               (time.time() - _t0) * 1000.0,
                               error_code=_cat.value if _cat else str(err)[:100],
                               agent_id=params.get("agent_id"))
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
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
            _gm_usage = (data or {}).get("usageMetadata", {})
            self._log_call(
                provider_ref, model_ref, True,
                (time.time() - _t0) * 1000.0,
                usage={
                    "prompt_tokens": _gm_usage.get("promptTokenCount", 0),
                    "completion_tokens": _gm_usage.get("candidatesTokenCount", 0),
                },
                agent_id=params.get("agent_id"))
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
                self._log_call(provider_ref, model_ref, False,
                               (time.time() - _t0) * 1000.0,
                               error_code=_cat.value if _cat else str(err)[:100],
                               agent_id=params.get("agent_id"))
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
