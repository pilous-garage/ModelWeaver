"""LiteLLMBridgeDefunct — Bridge universel via LiteLLM (legacy, non défaut).

Respecte le contrat déclaratif de BaseBridge (vérifié par hardcheck).
Bridge par défaut utilisé par BridgeRegistry quand aucun bridge natif
n'est enregistré pour un provider.
"""

import os
import sys
import json
import time
import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

from modules.llm_manager.base_bridge import (
    BaseBridge, ChatResponse, ModelCapabilities,
    ErrorCategory, BridgeError,
)
from modules.key_manager.key_manager import KeyLockedError

logger = logging.getLogger("modelweaver.bridge.litellm")

# Alias de provider_ref : certains provider_ref scrapés ne correspondent
# pas aux préfixes attendus par litellm pour le model ID.
_PROVIDER_ALIAS = {
    "google": "gemini",
}

# Traduction provider → variable d'environnement pour litellm
_PROVIDER_ENV = {
    "google": "GEMINI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "cohere": "COHERE_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
}


def _inject_env(provider_ref: str, api_key: str) -> None:
    """Injecte la clé API dans l'environnement pour litellm.

    Certains providers litellm lisent la clé depuis des noms de variable
    d'environnement spécifiques plutôt que depuis api_key=.
    """
    if not api_key:
        return
    env_var = _PROVIDER_ENV.get(provider_ref)
    if env_var and not os.environ.get(env_var):
        os.environ[env_var] = api_key


# ── ErrorClassifier ────────────────────────────────────────────

class ErrorClassifier:
    """Classifie les exceptions LiteLLM en BridgeError.

    Route prévue pour un futur module Analyseur d'erreur — l'interface
    est stable, l'implémentation pourra être enrichie plus tard via
    un module dédié (modules.llm_manager.error_analyzer).
    """

    @staticmethod
    def classify(exc: Exception,
                 provider_ref: str = "",
                 model_ref: str = "") -> BridgeError:
        msg = str(exc).lower()

        # Auth
        if any(k in msg for k in ("auth", "unauthorized", "401", "403",
                                   "api key", "invalid key", "no key",
                                   "permission denied", "not authenticated")):
            return BridgeError(ErrorCategory.AUTH, str(exc),
                               provider_ref, model_ref)

        # Rate limit
        if any(k in msg for k in ("rate limit", "429", "too many requests",
                                   "quota", "limit reached", "exhausted")):
            retry = None
            for token in ("retry-after", "retry_after", "retry after"):
                import re
                m = re.search(rf"{token}[\s:]+(\d+)", str(exc))
                if m:
                    retry = float(m.group(1))
                    break
            return BridgeError(ErrorCategory.RATE_LIMIT, str(exc),
                               provider_ref, model_ref,
                               retry_after_seconds=retry)

        # Context window exceeded
        if any(k in msg for k in ("context length", "context_window",
                                   "max context", "token limit",
                                   "too many tokens", "maximum context",
                                   "context_length_exceeded",
                                   "max_tokens")):
            detected = None
            for token in ("max_input_tokens", "context_window", "max_tokens"):
                import re
                m = re.search(rf"{token}[\s:]+(\d+)", str(exc))
                if m:
                    detected = int(m.group(1))
                    break
            return BridgeError(ErrorCategory.CONTEXT, str(exc),
                               provider_ref, model_ref,
                               detected_context_limit=detected)

        # Server / unavailable
        if any(k in msg for k in ("500", "502", "503", "504",
                                   "server error", "service unavailable",
                                   "connection error", "connection refused",
                                   "connection reset", "timeout",
                                   "bad gateway", "gateway timeout")):
            cat = ErrorCategory.TIMEOUT if "timeout" in msg else ErrorCategory.SERVER
            return BridgeError(cat, str(exc), provider_ref, model_ref)

        return BridgeError(ErrorCategory.UNKNOWN, str(exc),
                           provider_ref, model_ref)


# ── ContextValidator ───────────────────────────────────────────

class ContextValidator:
    """Valide et ajuste la fenêtre de contexte réelle d'un modèle.

    Maintient un cache local (dict) + persiste via provider_models.
    Le cache est volatil (runtime) ; la persistance est faite via
    la BDD catalogue (context_window_effective).
    """

    def __init__(self, cat=None):
        self.cat = cat
        self._effective_cache: Dict[str, int] = {}

    def get_effective_context(self, provider_ref: str,
                              model_ref: str) -> Optional[int]:
        """Fenêtre effective : mémoire > BDD > fallback None."""
        key = f"{provider_ref}/{model_ref}"
        if key in self._effective_cache:
            return self._effective_cache[key]
        if self.cat:
            cur = self.cat.conn.execute(
                "SELECT context_window_effective FROM provider_models "
                "WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?) "
                "AND model_id = (SELECT id FROM catalogue_models WHERE ref = ?)",
                (provider_ref, model_ref))
            row = cur.fetchone()
            if row and row[0] is not None:
                val = int(row[0])
                self._effective_cache[key] = val
                return val
        return None

    def on_context_error(self, provider_ref: str, model_ref: str,
                         tokens_sent: int,
                         detected_context_limit: int) -> int:
        """Enregistre l'échec et met à jour la fenêtre effective.

        Retourne la nouvelle limite recommandée (detected - marge 10%).
        """
        effective = int(detected_context_limit * 0.9)
        key = f"{provider_ref}/{model_ref}"
        self._effective_cache[key] = effective

        if self.cat:
            self.cat.conn.execute(
                "UPDATE provider_models SET context_window_effective = ?, "
                "updated_at = strftime('%s','now') "
                "WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?) "
                "AND model_id = (SELECT id FROM catalogue_models WHERE ref = ?)",
                (effective, provider_ref, model_ref))
            self.cat.conn.execute("""
                INSERT INTO context_audit_log
                    (provider_ref, model_ref, tokens_sent,
                     detected_context_limit, context_window_effective)
                VALUES (?, ?, ?, ?, ?)
            """, (provider_ref, model_ref, tokens_sent,
                  detected_context_limit, effective))
            self.cat.conn.commit()
        return effective


# ── LiteLLMBridgeDefunct ────────────────────────────────────────
# Ancien bridge via LiteLLM. Conservé pour compat (config llm.bridge=litellm)
# mais plus utilisé par défaut — les appels passent par LLMManager (façade)
# qui délègue à DirectBridge.

class LiteLLMBridgeDefunct(BaseBridge):
    """Bridge via LiteLLM (cloud + local OpenAI-compatible + Ollama).

    Ne dépend que de `litellm`. Pas besoin d'adaptateur par provider :
    LiteLLM gère la traduction provider→API.
    """

    def __init__(self, cat=None, km=None):
        self.cat = cat
        self.km = km
        self.classifier = ErrorClassifier()
        self.validator = ContextValidator(cat=cat)
        self._litellm = None

    def _lazy_import(self):
        if self._litellm is not None:
            return
        try:
            import litellm
            litellm.set_verbose = False
            self._litellm = litellm
        except ImportError:
            raise ImportError(
                "LiteLLM n'est pas installé. "
                "Exécutez : pip install litellm")

    def _resolve_key(self, provider_ref: str) -> tuple:
        """Retourne (api_key_string, api_base_string) pour un provider."""
        if self.km:
            try:
                rec = self.km.get_key(provider_ref)
                if rec and isinstance(rec, dict):
                    key = rec.get("api_key")
                    base = rec.get("api_base") or self._resolve_api_base(provider_ref)
                    return key, base
            except KeyLockedError:
                # Une clé verrouillée doit remonter telle quelle, pas être
                # masquée en « clé manquante » (qui donnerait une erreur auth
                # confuse). Les appelants la traitent comme une erreur claire.
                raise
            except Exception:
                pass
        return os.environ.get(f"{provider_ref.upper()}_API_KEY"), self._resolve_api_base(provider_ref)

    def _mark_model_unavailable(self, provider_ref: str, model_ref: str, error: str) -> None:
        """Marque un modèle comme indisponible dans provider_models_mapping."""
        if not self.cat:
            return
        try:
            self.cat.conn.execute("""
                UPDATE provider_models_mapping
                SET available = 0, last_error = ?, last_checked_at = strftime('%s','now')
                WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                  AND model_id = (SELECT id FROM catalogue_models WHERE ref = ?)
            """, (error[:500], provider_ref, model_ref))
            self.cat.conn.commit()
        except Exception:
            pass

    def _resolve_api_key(self, provider_ref: str) -> Optional[str]:
        key, _ = self._resolve_key(provider_ref)
        return key

    def _resolve_api_base(self, provider_ref: str) -> Optional[str]:
        if self.cat:
            cur = self.cat.conn.execute("""
                SELECT pe.endpoint_url
                FROM provider_endpoints pe
                JOIN catalogue_providers p ON p.id = pe.provider_id
                WHERE p.ref = ? AND pe.is_default = 1
                LIMIT 1
            """, (provider_ref,))
            row = cur.fetchone()
            if row:
                return row[0]
        return None

    def _has_custom_endpoint(self, provider_ref: str) -> bool:
        """True si le provider a un endpoint (api_base) personnalise defini
        dans le catalogue (ex: nvidia integrate.api.nvidia.com). Dans ce cas
        litellm doit etre appele en mode OpenAI-compatible (model sans prefix
        provider, api_base fourni)."""
        if self.cat:
            cur = self.cat.conn.execute("""
                SELECT pe.endpoint_url
                FROM provider_endpoints pe
                JOIN catalogue_providers p ON p.id = pe.provider_id
                WHERE p.ref = ? AND pe.is_default = 1
                LIMIT 1
            """, (provider_ref,))
            row = cur.fetchone()
            if row and row[0]:
                return True
        return False

    def _build_model_id(self, provider_ref: str,
                        model_ref: str) -> str:
        """Construit l'ID LiteLLM : provider/model ou provider_model_name.

        Si ``model_ref`` contient déjà le préfixe ``provider/``, on l'enlève
        pour éviter un double préfixage (ex: ``groq/groq/...``).
        """
        provider_ref = _PROVIDER_ALIAS.get(provider_ref, provider_ref)
        prefix = f"{provider_ref}/"
        if model_ref.startswith(prefix):
            model_ref = model_ref[len(prefix):]
        if self.cat:
            cur = self.cat.conn.execute("""
                SELECT kem.provider_model_name, p.api_type
                FROM provider_models_mapping kem
                JOIN catalogue_providers p ON p.id = kem.provider_id
                JOIN catalogue_models m ON m.id = kem.model_id
                WHERE p.ref = ? AND m.ref = ?
                LIMIT 1
            """, (provider_ref, model_ref))
            row = cur.fetchone()
            if row:
                pm_name, api_type = row["provider_model_name"], row["api_type"]
                # Provider a un endpoint personnalise (OpenAI-compatible) :
                # on retire le prefix provider pour que litellm route en
                # mode openai vers l'api_base fourni.
                if self._has_custom_endpoint(provider_ref):
                    return pm_name
                if api_type in ("anthropic", "gemini", "cohere", "bedrock",
                                "azure", "vertex", "databricks", "ollama"):
                    return pm_name if "/" in pm_name else f"{api_type}/{pm_name}"
                return f"{provider_ref}/{pm_name}"
        return f"{provider_ref}/{model_ref}"

    def _build_messages(self, messages: List[Dict[str, str]],
                        system_prompt: Optional[str] = None) -> list:
        if system_prompt:
            has_system = any(m.get("role") == "system" for m in messages)
            if not has_system:
                return [{"role": "system", "content": system_prompt}] + messages
        return messages

    # ── BaseBridge impl ────────────────────────────────────────

    def _budget_check(self, provider_ref: str, model_ref: str) -> Dict[str, Any]:
        try:
            from services.tarif import check_budget
            return check_budget(provider_ref, model_ref)
        except Exception:
            return {}

    def _budget_record(self, provider_ref: str, model_ref: str,
                       tokens: int = 0, requests: int = 1) -> Dict[str, Any]:
        try:
            from services.tarif import record_usage
            return record_usage(provider_ref, model_ref, tokens, requests)
        except Exception:
            return {}

    def _record_error_seq(self, provider_ref: str, model_ref: str,
                          outcome: str, error_code: str = "",
                          is_cost: bool = False) -> None:
        """Trace une séquence erreur/succès dans runtime_llm (quota_error_seq
        ou cost_error_seq) pour les analystes guess budget/cost."""
        try:
            from modules.sqlite.runtime_llm import db as rl_db
            d = rl_db()
            table = "cost_error_seq" if is_cost else "quota_error_seq"
            # Résolution du guess le plus proche (target provider)
            bundle_id = 0
            guess_id = 0
            try:
                from modules.sqlite.budget_cost import db as bc_db, read as bc_read
                bcd = bc_db()
                bundles = bc_read.list_guess_bundles(bcd,
                    target_kind="provider", target_ref=provider_ref)
                if bundles:
                    bundle_id = bundles[0]["bundle_id"]
                    guesses = (bc_read.list_guesses(bcd, bundle_id)
                               if not is_cost else bc_read.list_guesses_cost(bcd, bundle_id))
                    if guesses:
                        guess_id = guesses[0]["guess_id"]
                bcd.close()
            except Exception:
                pass
            d._conn.execute(
                f"INSERT INTO {table} (bundle_id, guess_id, target_ref, "
                f"type_limite, outcome, error_code) VALUES (?,?,?,?,?,?)",
                (bundle_id, guess_id, provider_ref, model_ref, outcome, error_code))
            d._conn.commit()
            d.close()
        except Exception:
            pass

    def chat(self, provider_ref: str, model_ref: str,
              messages: List[Dict[str, str]],
              temperature: float = 0.7,
              max_tokens: Optional[int] = None,
              system_prompt: Optional[str] = None,
              stream: bool = False,
              agent_id: Optional[str] = None,
               **params) -> ChatResponse:
        self._lazy_import()
        model_id = self._build_model_id(provider_ref, model_ref)
        msgs = self._build_messages(messages, system_prompt)
        api_key, api_base = self._resolve_key(provider_ref)
        _inject_env(provider_ref, api_key or "")

        # Budget check avant appel
        budget_check = self._budget_check(provider_ref, model_ref)
        if not budget_check.get("ok", True):
            raise BridgeError(
                category=ErrorCategory.RATE_LIMIT,
                message="Budget épuisé pour ce fournisseur/modèle",
                provider_ref=provider_ref,
                model_ref=model_ref,
            )

        kwargs = dict(
            model=model_id,
            messages=msgs,
            temperature=temperature,
            api_key=api_key,
            api_base=api_base,
            stream=stream,
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        kwargs.update(params)

        # Check context window effective avant l'appel
        effective = self.validator.get_effective_context(
            provider_ref, model_ref)
        if effective and max_tokens is None:
            kwargs["max_tokens"] = effective

        t0 = time.time()
        try:
            response = self._litellm.completion(**kwargs)
            elapsed_ms = int((time.time() - t0) * 1000)
            tokens = 0
            usage = {}
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                tokens = (response.usage.prompt_tokens or 0) + (response.usage.completion_tokens or 0)
            budget = self._budget_record(provider_ref, model_ref, tokens=tokens, requests=1)
            self._log_call(provider_ref, model_ref, "ok", agent_id=agent_id,
                           tokens_in=response.usage.prompt_tokens or 0,
                           tokens_out=response.usage.completion_tokens or 0,
                           tokens_thinking=self._extract_thinking(response),
                           latency_ms=elapsed_ms)

            # Extraire tool_calls de la réponse
            msg = response.choices[0].message
            tool_calls = None
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tool_calls = []
                for tc in msg.tool_calls:
                    tc_data = {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    tool_calls.append(tc_data)

            return ChatResponse(
                content=msg.content or "",
                model=response.model,
                finish_reason=response.choices[0].finish_reason or "stop",
                usage=usage,
                raw=response,
                budget=budget,
                tool_calls=tool_calls,
            )
        except Exception as e:
            be = self.classifier.classify(e, provider_ref, model_ref)
            if be.category == ErrorCategory.CONTEXT and be.detected_context_limit:
                total_tokens = self._estimate_tokens(msgs)
                new_limit = self.validator.on_context_error(
                    provider_ref, model_ref, total_tokens,
                    be.detected_context_limit)
                kwargs["max_tokens"] = new_limit
                try:
                    response = self._litellm.completion(**kwargs)
                    elapsed_ms = int((time.time() - t0) * 1000)
                    tokens = 0
                    usage = {}
                    if response.usage:
                        usage = {
                            "prompt_tokens": response.usage.prompt_tokens,
                            "completion_tokens": response.usage.completion_tokens,
                            "total_tokens": response.usage.total_tokens,
                        }
                        tokens = (response.usage.prompt_tokens or 0) + (response.usage.completion_tokens or 0)
                    budget = self._budget_record(provider_ref, model_ref, tokens=tokens, requests=1)
                    self._log_call(provider_ref, model_ref, "ok", agent_id=agent_id,
                                   tokens_in=response.usage.prompt_tokens or 0,
                                   tokens_out=response.usage.completion_tokens or 0,
                                   tokens_thinking=self._extract_thinking(response),
                                   latency_ms=elapsed_ms)
                    msg2 = response.choices[0].message
                    tool_calls2 = None
                    if hasattr(msg2, "tool_calls") and msg2.tool_calls:
                        tool_calls2 = []
                        for tc in msg2.tool_calls:
                            tool_calls2.append({"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}})
                    return ChatResponse(
                        content=msg2.content or "",
                        model=response.model,
                        finish_reason=response.choices[0].finish_reason or "stop",
                        usage=usage,
                        raw=response,
                        budget=budget,
                        tool_calls=tool_calls2,
                    )
                except Exception as e2:
                    be2 = self.classifier.classify(e2, provider_ref, model_ref)
                    if be2.category in (ErrorCategory.AUTH, ErrorCategory.UNKNOWN):
                        self._mark_model_unavailable(provider_ref, model_ref, str(e2)[:500])
                    elapsed_ms = int((time.time() - t0) * 1000)
                    self._record_error_seq(provider_ref, model_ref, "error",
                                           error_code=getattr(be2, "code", ""), is_cost=False)
                    self._log_call(provider_ref, model_ref,
                                   "quota_exhausted" if be2.category == ErrorCategory.RATE_LIMIT
                                   else "error",
                                   agent_id=agent_id,
                                   error_code=getattr(be2, "code", None),
                                   error_detail=str(e2)[:500],
                                   latency_ms=elapsed_ms)
                    raise be2
            elapsed_ms = int((time.time() - t0) * 1000)
            self._record_error_seq(provider_ref, model_ref, "error",
                                   error_code=getattr(be, "code", ""), is_cost=False)
            self._log_call(provider_ref, model_ref,
                           "quota_exhausted" if be.category == ErrorCategory.RATE_LIMIT
                           else "error",
                           agent_id=agent_id,
                           error_code=getattr(be, "code", None),
                           error_detail=str(e)[:500],
                           latency_ms=elapsed_ms)
            if be.category in (ErrorCategory.AUTH, ErrorCategory.UNKNOWN):
                self._mark_model_unavailable(provider_ref, model_ref, str(e)[:500])
            raise be

    def chat_stream(self, provider_ref: str, model_ref: str,
                     messages: List[Dict[str, str]],
                     temperature: float = 0.7,
                     max_tokens: Optional[int] = None,
                     system_prompt: Optional[str] = None,
                     agent_id: Optional[str] = None,
                     **params) -> Iterator[str]:
        self._lazy_import()
        model_id = self._build_model_id(provider_ref, model_ref)
        msgs = self._build_messages(messages, system_prompt)
        api_key, api_base = self._resolve_key(provider_ref)
        _inject_env(provider_ref, api_key or "")

        # Budget check avant appel
        budget_check = self._budget_check(provider_ref, model_ref)
        if not budget_check.get("ok", True):
            raise BridgeError(
                category=ErrorCategory.RATE_LIMIT,
                message="Budget épuisé pour ce fournisseur/modèle",
                provider_ref=provider_ref,
                model_ref=model_ref,
            )

        kwargs = dict(
            model=model_id,
            messages=msgs,
            temperature=temperature,
            api_key=api_key,
            api_base=api_base,
            stream=True,
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        kwargs.update(params)

        t0 = time.time()
        char_count = 0
        ok = True
        try:
            for chunk in self._litellm.completion(**kwargs):
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    char_count += len(delta.content)
                    yield delta.content
        except Exception as e:
            ok = False
            elapsed_ms = int((time.time() - t0) * 1000)
            be = self.classifier.classify(e, provider_ref, model_ref)
            self._log_call(provider_ref, model_ref,
                           "quota_exhausted" if be.category == ErrorCategory.RATE_LIMIT
                           else "error",
                           agent_id=agent_id,
                           error_code=getattr(be, "code", None),
                           error_detail=str(e)[:500],
                           latency_ms=elapsed_ms)
            raise be
        finally:
            elapsed_ms = int((time.time() - t0) * 1000)
            if char_count:
                tokens = max(1, char_count // 4)
                self._budget_record(provider_ref, model_ref, tokens=tokens, requests=1)
                if ok:
                    self._log_call(provider_ref, model_ref, "ok", agent_id=agent_id,
                                   tokens_out=tokens, latency_ms=elapsed_ms)

    def get_capabilities(self, provider_ref: str,
                         model_ref: str) -> ModelCapabilities:
        self._lazy_import()
        ctx = self.validator.get_effective_context(provider_ref, model_ref)

        if self.cat:
            cur = self.cat.conn.execute("""
                SELECT pm.context_window_tokens, pm.max_output_tokens,
                       pm.cost_per_input_token, pm.cost_per_output_token,
                       pm.context_window_effective
                FROM provider_models pm
                JOIN catalogue_providers p ON p.id = pm.provider_id
                JOIN catalogue_models m ON m.id = pm.model_id
                WHERE p.ref = ? AND m.ref = ?
                LIMIT 1
            """, (provider_ref, model_ref))
            row = cur.fetchone()
            if row:
                context = ctx or row["context_window_tokens"] or row["context_window_effective"] or 4096
                return ModelCapabilities(
                    context_window=int(context),
                    max_output=row["max_output_tokens"] or 1024,
                    cost_input_per_1k=float(row["cost_per_input_token"]) / 1000
                        if row["cost_per_input_token"] else None,
                    cost_output_per_1k=float(row["cost_per_output_token"]) / 1000
                        if row["cost_per_output_token"] else None,
                )

        # Fallback litellm.model_cost
        model_id = self._build_model_id(provider_ref, model_ref)
        try:
            info = self._litellm.model_cost.get(model_id, {})
            return ModelCapabilities(
                context_window=ctx or info.get("max_input_tokens", 4096),
                max_output=info.get("max_output_tokens", 1024),
                cost_input_per_1k=info.get("input_cost_per_token"),
                cost_output_per_1k=info.get("output_cost_per_token"),
                supports_vision=info.get("supports_vision", False),
                supports_function_calling=info.get("supports_function_calling", False),
            )
        except Exception:
            return ModelCapabilities(
                context_window=ctx or 4096, max_output=1024)

    def list_available_providers(self) -> List[Dict[str, Any]]:
        self._lazy_import()
        result = []
        if self.cat:
            cur = self.cat.conn.execute(
                "SELECT ref, name, provider_type, api_type "
                "FROM catalogue_providers ORDER BY name")
            for row in cur.fetchall():
                entry = dict(row)
                entry["bridge"] = "litellm"
                provider_ref = row["ref"]
                api_key = self._resolve_api_key(provider_ref)
                if api_key or row["provider_type"] in ("ollama", "local", "builtin"):
                    entry["available"] = True
                else:
                    entry["available"] = False
                    entry["missing_key"] = True
                result.append(entry)
        return result

    def list_available_models(self,
                              provider_ref: str) -> List[Dict[str, Any]]:
        if self.cat:
            cur = self.cat.conn.execute("""
                SELECT DISTINCT m.ref, m.name, m.developer,
                       kem.provider_model_name,
                       MAX(CASE WHEN mc.capability = 'supports_function_calling' THEN
                                 CASE WHEN mc.value = 'true' AND mc.confidence > 0.5 THEN 1 ELSE 0 END
                       END) AS supports_function_calling,
                       MAX(CASE WHEN mc.capability = 'supports_vision' THEN
                                 CASE WHEN mc.value = 'true' AND mc.confidence > 0.5 THEN 1 ELSE 0 END
                       END) AS supports_vision,
                       MAX(CASE WHEN mc.capability = 'streaming' THEN
                                 CASE WHEN mc.value = 'true' AND mc.confidence > 0.5 THEN 1 ELSE 0 END
                       END) AS supports_streaming,
                       MAX(CASE WHEN mc.capability = 'chat' THEN
                                 CASE WHEN mc.value = 'true' AND mc.confidence > 0.5 THEN 1 ELSE 0 END
                       END) AS supports_chat,
                       mc.source as cap_source
                FROM provider_models_mapping kem
                JOIN catalogue_models m ON m.id = kem.model_id
                JOIN catalogue_providers p ON p.id = kem.provider_id
                LEFT JOIN model_capability mc ON mc.model_id = m.id
                WHERE p.ref = ?
                GROUP BY m.id
                ORDER BY kem.available DESC, m.name
            """, (provider_ref,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        return []

    def health_check(self, provider_ref: Optional[str] = None) -> Dict[str, Any]:
        self._lazy_import()
        if not provider_ref:
            return {"bridge": "litellm", "status": "loaded",
                    "litellm_version": getattr(self._litellm, "__version__", "?")}
        try:
            caps = self.get_capabilities(provider_ref, "")
            return {"bridge": "litellm", "provider": provider_ref,
                    "status": "ok", "models_count": len(self.list_available_models(provider_ref))}
        except Exception as e:
            return {"bridge": "litellm", "provider": provider_ref,
                    "status": "error", "error": str(e)}

    def classify_error(self, error: Any,
                       provider_ref: str = "",
                       model_ref: str = "") -> BridgeError:
        return self.classifier.classify(error, provider_ref, model_ref)

    # ── Journalisation des appels reels ──────────────────────────
    # Les appels ne touchent PLUS SQLite directement (risque « database is
    # locked » en concurrence) : on pousse une ligne JSON dans le journal
    # disque append-only (modules/usage/usage_log). Le rassembleur
    # (usage_collector.py) consolide ce journal dans real_call_models /
    # endpoint_model_usage / agent_actif de façon asynchrone, et degrade
    # provider_models_mapping.available sur echec.
    @staticmethod
    def _extract_thinking(response) -> int:
        """Tokens de raisonnement (tokens_thinking) depuis la réponse litellm.

        Priorité : completion_tokens_details.reasoning_tokens (OpenAI),
        puis reasoning_tokens direct sur usage (Anthropic/autres).
        """
        try:
            usage = getattr(response, "usage", None)
            if usage is None:
                return 0
            details = getattr(usage, "completion_tokens_details", None)
            if details is not None:
                rt = getattr(details, "reasoning_tokens", None)
                if rt:
                    return int(rt)
            rt = getattr(usage, "reasoning_tokens", None)
            if rt:
                return int(rt)
            return 0
        except Exception:
            return 0

    def _log_call(self, provider_ref, model_ref, status, agent_id=None,
                  tokens_in=0, tokens_out=0, tokens_thinking=0, cost=0.0,
                  error_code=None, error_detail=None, sent_at=None,
                  latency_ms=None):
        """Journalise un appel LLM reel sur disque (append atomique).
        Best-effort : n'interrompt jamais le flux principal."""
        try:
            import time as _t
            now = int(_t.time())
            # endpoint utilise (api_base custom si defini)
            endpoint_id = None
            if self.cat:
                row = self.cat.conn.execute("""
                    SELECT pe.endpoint_id FROM provider_endpoints pe
                    JOIN catalogue_providers p ON p.id = pe.provider_id
                    WHERE p.ref = ? AND pe.is_default = 1 LIMIT 1
                """, (provider_ref,)).fetchone()
                if row:
                    endpoint_id = row[0]
            key_ref = None
            try:
                info = self.km.get_key(provider_ref) if self.km else None
                key_ref = info.get("ref") if info else None
            except Exception:
                pass
            from modules.usage import usage_log
            usage_log.log_call(
                provider_ref, model_ref, status,
                agent_id=agent_id, endpoint_id=endpoint_id, key_ref=key_ref,
                tokens_in=tokens_in, tokens_out=tokens_out,
                tokens_thinking=tokens_thinking, cost=cost,
                error_code=error_code, error_detail=error_detail,
                sent_at=sent_at, received_at=now,
                latency_ms=latency_ms,
            )
            # Miroir live agent (etat, non critique) — best-effort.
            if agent_id:
                self._touch_agent_actif(agent_id, tokens_in + tokens_out)
        except Exception:
            pass

    def _touch_agent_actif(self, agent_id, tokens):
        """Upsert leagre dans agent_actif (etat live). Best-effort."""
        try:
            import time as _t
            now = int(_t.time())
            conn = self._get_mw_conn()
            if conn is None:
                return
            conn.execute("""
                INSERT INTO agent_actif
                    (agent_id, status, last_heartbeat, calls_count, tokens_total, updated_at)
                VALUES (?, 'RUNNING', ?, 1, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    last_heartbeat = excluded.last_heartbeat,
                    calls_count = calls_count + 1,
                    tokens_total = tokens_total + excluded.tokens_total,
                    updated_at = excluded.updated_at
            """, (str(agent_id), now, int(tokens or 0), now))
            conn.commit()
        except Exception:
            pass

    def _get_mw_conn(self):
        """Connexion modelweaver.db reutilisee (une seule par bridge) pour
        agent_actif. Ouverte lazy, jamais fermee explicitement (duree de vie
        du bridge)."""
        if getattr(self, "_mw_conn", None) is None:
            try:
                from modules.sql.db import ModelWeaverDB
                self._mw_conn = ModelWeaverDB().conn
            except Exception:
                self._mw_conn = None
        return self._mw_conn

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(messages: list) -> int:
        total = 0
        for m in messages:
            total += len(m.get("content", "").split())
        return int(total * 1.3)  # approximation grossière ~1.3 token/mot