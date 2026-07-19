"""LiteLLMBridge — Implémentation concrète de BaseBridge via LiteLLM.

Respecte le contrat déclaratif de BaseBridge (vérifié par hardcheck). 
Seul bridge officiel fourni ; l'utilisateur peut en brancher d'autres.
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


# ── LiteLLMBridge ──────────────────────────────────────────────

class LiteLLMBridge(BaseBridge):
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
        """Construit l'ID LiteLLM : provider/model ou provider_model_name."""
        if self.cat:
            cur = self.cat.conn.execute("""
                SELECT kem.provider_model_name, p.api_type
                FROM key_endpoint_models kem
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

    def chat(self, provider_ref: str, model_ref: str,
             messages: List[Dict[str, str]],
             temperature: float = 0.7,
             max_tokens: Optional[int] = None,
             system_prompt: Optional[str] = None,
             stream: bool = False,
             **params) -> ChatResponse:
        self._lazy_import()
        model_id = self._build_model_id(provider_ref, model_ref)
        msgs = self._build_messages(messages, system_prompt)
        api_key, api_base = self._resolve_key(provider_ref)

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

        try:
            response = self._litellm.completion(**kwargs)
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
            self._log_call(provider_ref, model_ref, "ok",
                           tokens_in=response.usage.prompt_tokens or 0,
                           tokens_out=response.usage.completion_tokens or 0)
            return ChatResponse(
                content=response.choices[0].message.content or "",
                model=response.model,
                finish_reason=response.choices[0].finish_reason or "stop",
                usage=usage,
                raw=response,
                budget=budget,
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
                    self._log_call(provider_ref, model_ref, "ok",
                                   tokens_in=response.usage.prompt_tokens or 0,
                                   tokens_out=response.usage.completion_tokens or 0)
                    return ChatResponse(
                        content=response.choices[0].message.content or "",
                        model=response.model,
                        finish_reason=response.choices[0].finish_reason or "stop",
                        usage=usage,
                        raw=response,
                        budget=budget,
                    )
                except Exception as e2:
                    be2 = self.classifier.classify(e2, provider_ref, model_ref)
                    self._log_call(provider_ref, model_ref,
                                   "quota_exhausted" if be2.category == ErrorCategory.RATE_LIMIT
                                   else "error",
                                   error_code=getattr(be2, "code", None),
                                   error_detail=str(e2)[:500])
                    raise be2
            self._log_call(provider_ref, model_ref,
                           "quota_exhausted" if be.category == ErrorCategory.RATE_LIMIT
                           else "error",
                           error_code=getattr(be, "code", None),
                           error_detail=str(e)[:500])
            raise be

    def chat_stream(self, provider_ref: str, model_ref: str,
                    messages: List[Dict[str, str]],
                    temperature: float = 0.7,
                    max_tokens: Optional[int] = None,
                    system_prompt: Optional[str] = None,
                    **params) -> Iterator[str]:
        self._lazy_import()
        model_id = self._build_model_id(provider_ref, model_ref)
        msgs = self._build_messages(messages, system_prompt)
        api_key, api_base = self._resolve_key(provider_ref)

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
            be = self.classifier.classify(e, provider_ref, model_ref)
            self._log_call(provider_ref, model_ref,
                           "quota_exhausted" if be.category == ErrorCategory.RATE_LIMIT
                           else "error",
                           error_code=getattr(be, "code", None),
                           error_detail=str(e)[:500])
            raise be
        finally:
            if char_count:
                tokens = max(1, char_count // 4)
                self._budget_record(provider_ref, model_ref, tokens=tokens, requests=1)
                if ok:
                    self._log_call(provider_ref, model_ref, "ok", tokens_out=tokens)

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
                       kem.provider_model_name
                FROM key_endpoint_models kem
                JOIN catalogue_models m ON m.id = kem.model_id
                JOIN catalogue_providers p ON p.id = kem.provider_id
                WHERE p.ref = ? AND kem.available = 1
                ORDER BY m.name
            """, (provider_ref,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        return []

    def health_check(self,
                     provider_ref: Optional[str] = None) -> Dict[str, Any]:
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

    # ── Journalisation des appels reels (real_call_models) ─────────
    def _log_call(self, provider_ref, model_ref, status, agent_id=None,
                  tokens_in=0, tokens_out=0, cost=0.0, error_code=None,
                  error_detail=None, sent_at=None):
        """Logge un appel LLM reel dans modelweaver.db (real_call_models)
        et met a jour endpoint_model_usage + degrade available si echec.
        Best-effort : n'interrompt jamais le flux principal."""
        try:
            from modules.sql.db import ModelWeaverDB
            now = int(__import__("time").time())
            sent = sent_at or now
            conn = ModelWeaverDB().conn
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
            conn.execute("""
                INSERT INTO real_call_models
                    (provider_ref, endpoint_id, key_ref, model_ref, agent_id,
                     sent_at, received_at, tokens_in, tokens_out, cost,
                     status, error_code, error_detail, window_key)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (provider_ref, endpoint_id, key_ref, model_ref, agent_id,
                  sent, now, tokens_in, tokens_out, cost, status,
                  error_code, error_detail,
                  __import__("time").strftime("%Y-%m-%d-%H", __import__("time").gmtime(now)))),
            # maj endpoint_model_usage (upsert sans contrainte UNIQUE)
            cur = conn.execute(
                "SELECT id FROM endpoint_model_usage WHERE model_ref = ? "
                "ORDER BY id DESC LIMIT 1", (model_ref,))
            row = cur.fetchone()
            if row:
                conn.execute("""
                    UPDATE endpoint_model_usage SET
                        requests = requests + 1,
                        tokens_in = tokens_in + ?,
                        tokens_out = tokens_out + ?,
                        cost = cost + ?,
                        last_call_at = ?,
                        last_call_working = ?,
                        error_count = error_count + ?
                    WHERE id = ?
                """, (tokens_in, tokens_out, cost, now,
                      1 if status == "ok" else 0,
                      0 if status == "ok" else 1, row["id"]))
            else:
                conn.execute("""
                    INSERT INTO endpoint_model_usage
                        (endpoint_id, model_ref, agent_id, requests, tokens_in,
                         tokens_out, cost, last_call_at, last_call_working, error_count)
                    VALUES (?,?,?,1,?,?,?,?,?,?)
                """, (endpoint_id, model_ref, agent_id, tokens_in, tokens_out,
                      cost, now, 1 if status == "ok" else 0,
                      0 if status == "ok" else 1)),
            # degrade available si echec (sera re-upgrade au refresh)
            if status != "ok" and self.cat and endpoint_id is not None:
                self.cat.conn.execute("""
                    UPDATE key_endpoint_models SET available = 0
                    WHERE endpoint_id = ? AND model_id = (
                        SELECT id FROM catalogue_models WHERE ref = ?
                    ) AND key_ref = ?
                """, (endpoint_id, model_ref, key_ref or ""))
            conn.commit()
        except Exception:
            pass

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(messages: list) -> int:
        total = 0
        for m in messages:
            total += len(m.get("content", "").split())
        return int(total * 1.3)  # approximation grossière ~1.3 token/mot