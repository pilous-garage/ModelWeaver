"""LLM Manager — Catalogue et recommandation de modèles LLM.

Re-exporte les symboles du bridge pour satisfaire le contrat hardcheck.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional

from modules.llm_manager.base_bridge import (
    BaseBridge, ModelCapabilities, ChatResponse, BridgeError, ErrorCategory,
)
from modules.llm_manager import catalogue_remote

_DATA_DIR = Path(__file__).resolve().parent / "data"


# ── Matrice de recommandation ──────────────────────────────────
# Chaque entrée : (use_case, technical_level) → liste de refs
# technical_level: "free" (API gratuite), "paid" (API payante), "local" (local Ollama)
RECOMMENDATIONS = {
    # Codage
    ("coding", "paid"): [
        {"ref": "claude-sonnet-4", "provider": "anthropic", "reason": "Meilleur modèle codage (2025)"},
        {"ref": "gpt-4o", "provider": "openai", "reason": "Excellent équilibre code/prix"},
        {"ref": "deepseek-v3", "provider": "deepseek", "reason": "Très bon codage, prix bas"},
    ],
    ("coding", "free"): [
        {"ref": "gemini-2.5-flash", "provider": "github-models", "reason": "Gratuit via GitHub Models"},
        {"ref": "llama-3.3-70b", "provider": "groq", "reason": "Gratuit via Groq (ultra-rapide)"},
        {"ref": "gpt-4o-mini", "provider": "github-models", "reason": "Gratuit via GitHub Models"},
    ],
    ("coding", "local"): [
        {"ref": "qwen-2.5-coder-32b", "provider": "ollama", "reason": "Excellent codage local, 32B"},
        {"ref": "deepseek-v3", "provider": "ollama", "reason": "Codage + généraliste, 671B MoE"},
        {"ref": "phi-4", "provider": "ollama", "reason": "Léger (14B), bon codage, PC milieu"},
    ],
    # Chat / dialogue général
    ("chat", "paid"): [
        {"ref": "gpt-4o", "provider": "openai", "reason": "Meilleur équilibre général"},
        {"ref": "claude-sonnet-4", "provider": "anthropic", "reason": "Excellent dialogue nuancé"},
        {"ref": "gemini-2.5-pro", "provider": "google", "reason": "Très long contexte (1M tokens)"},
    ],
    ("chat", "free"): [
        {"ref": "gemini-3.5-flash-lite", "provider": "google", "reason": "Gratuit, très long contexte"},
        {"ref": "llama-3.3-70b", "provider": "groq", "reason": "Gratuit, ultra-rapide"},
        {"ref": "gpt-4o-mini", "provider": "github-models", "reason": "Gratuit via GitHub Models"},
    ],
    ("chat", "local"): [
        {"ref": "llama-3.3-70b", "provider": "ollama", "reason": "Meilleur généraliste local"},
        {"ref": "mixtral-8x22b", "provider": "ollama", "reason": "Bon équilibre, MoE efficace"},
        {"ref": "qwen-2.5-72b", "provider": "ollama", "reason": "Très bon généraliste open"},
    ],
    # Analyse / raisonnement
    ("analysis", "paid"): [
        {"ref": "claude-opus-4", "provider": "anthropic", "reason": "Meilleur pour analyse profonde"},
        {"ref": "o1", "provider": "openai", "reason": "Raisonnement pas-à-pas puissant"},
        {"ref": "gemini-2.5-pro", "provider": "google", "reason": "Long contexte, multimodal"},
    ],
    ("analysis", "free"): [
        {"ref": "gemini-3.5-flash-lite", "provider": "google", "reason": "Gratuit, bon raisonnement"},
        {"ref": "deepseek-r1", "provider": "groq", "reason": "Gratuit, raisonnement pas-à-pas"},
    ],
    ("analysis", "local"): [
        {"ref": "deepseek-r1", "provider": "ollama", "reason": "Raisonnement local, 70B"},
        {"ref": "qwen-2.5-72b", "provider": "ollama", "reason": "Bon pour analyse généraliste"},
    ],
    # Écriture / créatif
    ("writing", "paid"): [
        {"ref": "claude-sonnet-4", "provider": "anthropic", "reason": "Meilleur style et nuance"},
        {"ref": "gpt-4o", "provider": "openai", "reason": "Polyvalence créative"},
        {"ref": "mistral-large", "provider": "mistral", "reason": "Bon pour texte long français"},
    ],
    ("writing", "free"): [
        {"ref": "gemini-3.5-flash-lite", "provider": "google", "reason": "Gratuit, long contexte"},
        {"ref": "llama-3.3-70b", "provider": "groq", "reason": "Gratuit, génération rapide"},
    ],
    ("writing", "local"): [
        {"ref": "llama-3.3-70b", "provider": "ollama", "reason": "Meilleur style local"},
        {"ref": "mixtral-8x22b", "provider": "ollama", "reason": "Créatif, MoE 141B"},
        {"ref": "qwen-2.5-72b", "provider": "ollama", "reason": "Bon pour texte long"},
    ],
}

# ── Pré-requis par use_case ───────────────────────────────────
USE_CASE_REQUIREMENTS = {
    "coding":    {"features": ["function_calling", "chat"]},
    "chat":      {"features": ["chat"]},
    "simple":    {"features": []},  # tout modèle fait l'affaire
    "writing":   {"features": ["chat"]},
    "analysis":  {"features": ["chat"]},
    "embedding": {"features": ["embedding"]},
}


# ── Modèles de confiance (testés en priorité) ──────────────────
TRUSTED_MODELS = [
    "nvidia/stepfun-ai/step-3.7-flash",
    "groq/llama-3.3-70b-versatile",
    "groq/llama-3.1-8b-instant",
]


class LLMManager:
    """Gestionnaire de catalogue LLM : consultation et recommandation."""

    def __init__(self, cat, km=None):
        self.cat = cat
        self.km = km
        self._bridge = None

    # ── Façade bridge (DirectBridge par défaut) ──────────────────

    def _bridge_name(self) -> str:
        """Nom du bridge actif (config ``llm.bridge``, défaut ``direct``)."""
        try:
            from modules.config.config_manager import config
            return str(config.get("llm.bridge", "direct")).lower()
        except Exception:
            return "direct"

    def get_bridge(self) -> BaseBridge:
        """Retourne le bridge actif (créé une fois, sélection par config).

        litellm est retiré (legacy) : tout passe par DirectBridge (appels
        OpenAI-compatibles natifs)."""
        if self._bridge is None:
            from modules.llm_manager.direct_bridge import DirectBridge
            self._bridge = DirectBridge(cat=self.cat, km=self.km)
        return self._bridge

    def chat(self, provider_ref: str, model_ref: str,
             messages: List[Dict[str, str]], **params) -> Optional[ChatResponse]:
        """Appelle le LLM via le bridge actif."""
        return self.get_bridge().chat(provider_ref, model_ref, messages, **params)

    def chat_stream(self, provider_ref: str, model_ref: str,
                    messages: List[Dict[str, str]], **params):
        """Appelle le LLM en streaming via le bridge actif."""
        return self.get_bridge().chat_stream(provider_ref, model_ref, messages, **params)

    def list_available_models(self, provider_ref: str) -> List[Dict[str, Any]]:
        """Liste les modèles disponibles d'un provider via le bridge actif."""
        return self.get_bridge().list_available_models(provider_ref)

    def probe(self, provider_ref: Optional[str] = None,
              model_ref: Optional[str] = None,
              timeout: float = 12.0) -> Dict[str, Any]:
        """Probe la disponibilité réelle des modèles (voir DirectBridge.probe).

        - probe()                  → tous les providers
        - probe(provider)          → tous les modèles du provider
        - probe(provider, model)   → un seul modèle
        `timeout` = timeout réseau par probe. Threads simultanés ≤ 20.
        Marque unavailable les modèles en échec API.
        """
        return self.get_bridge().probe(provider_ref, model_ref, timeout)

    def list_available_providers(self) -> List[Dict[str, Any]]:
        """Liste les providers disponibles via le bridge actif."""
        return self.get_bridge().list_available_providers()

    def get_capabilities(self, provider_ref: str,
                         model_ref: str) -> ModelCapabilities:
        """Retourne les capacités d'un modèle via le bridge actif."""
        return self.get_bridge().get_capabilities(provider_ref, model_ref)

    def health_check(self, provider_ref: Optional[str] = None) -> Dict[str, Any]:
        """Vérifie la santé d'un provider via le bridge actif."""
        return self.get_bridge().health_check(provider_ref)

    def classify_error(self, error: Any,
                       provider_ref: str = "",
                       model_ref: str = "") -> BridgeError:
        """Classifie une erreur via le bridge actif."""
        return self.get_bridge().classify_error(error, provider_ref, model_ref)

    def list_providers(self) -> List[Dict[str, Any]]:
        cur = self.cat.conn.execute(
            "SELECT ref, name, provider_type, api_type, website, is_free_tier_provider "
            "FROM catalogue_providers ORDER BY name")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def list_models(self, provider_ref: Optional[str] = None) -> List[Dict[str, Any]]:
        if provider_ref:
            cur = self.cat.conn.execute("""
                SELECT m.ref, m.name, m.developer, m.release_year, m.architecture,
                       m.parameter_count, m.modality, m.target_use, m.license,
                       m.is_open_weights, pm.provider_model_name,
                       pm.context_window_tokens, pm.cost_per_input_token,
                       pm.cost_per_output_token, pm.status,
                       p.ref as provider_ref, p.name as provider_name
                FROM catalogue_models m
                JOIN provider_models pm ON pm.model_id = m.id
                JOIN catalogue_providers p ON p.id = pm.provider_id
                WHERE p.ref = ?
                ORDER BY m.name
            """, (provider_ref,))
        else:
            cur = self.cat.conn.execute("""
                SELECT m.ref, m.name, m.developer, m.release_year, m.architecture,
                       m.parameter_count, m.modality, m.target_use, m.license,
                       m.is_open_weights, pm.provider_model_name,
                       pm.context_window_tokens, pm.cost_per_input_token,
                       pm.cost_per_output_token, pm.status,
                       p.ref as provider_ref, p.name as provider_name
                FROM catalogue_models m
                JOIN provider_models pm ON pm.model_id = m.id
                JOIN catalogue_providers p ON p.id = pm.provider_id
                ORDER BY p.name, m.name
            """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_model(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.cat.conn.execute("""
            SELECT m.ref, m.name, m.developer, m.release_year, m.architecture,
                   m.parameter_count, m.modality, m.target_use, m.license,
                   m.is_open_weights
            FROM catalogue_models m
            WHERE m.ref = ?
        """, (ref,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def recommend(self, use_case: str = "chat",
                  technical_level: str = "free") -> Dict[str, Any]:
        key = (use_case, technical_level)
        recs = RECOMMENDATIONS.get(key, [])
        enriched = []
        for r in recs:
            model = self.get_model(r["ref"])
            if model:
                enriched.append({**r, "model": model})
        return {
            "use_case": use_case,
            "technical_level": technical_level,
            "recommendations": enriched,
            "count": len(enriched),
        }

    def assign_llm(self, use_case: str = "coding",
                   exclude_provider: Optional[str] = None,
                   exclude_model: Optional[str] = None,
                   exclude_providers: Optional[list] = None,
                   exclude_models: Optional[list] = None,
                   max_candidates: int = 8,
                   min_window: int = 0,
                   agent_id: Optional[str] = None,
                   latence_penalise: float = 1.0,
                   latence_regule: float = 60.0,
                   task_context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Trouve un LLM disponible via le service LLM Manager (ou fallback).

        Si le service LLM Manager (socket llm.sock) est disponible, on lui
        délègue l'allocation (il respecte l'état global : noretryuntil,
        budgets, exclusions, anti-affinité entre agents). Sinon, fallback
        local en interrogeant les providers via DirectBridge.
        ``agent_id`` permet l'anti-affinité : un modèle pris par un autre agent
        actif n'est pas ré-alloué (les agents parallèles ont des modèles
        différents, sinon les RPM bas se saturent).
        ``latence_penalise``/``latence_regule`` (s) : tolérance latence pour le
        calcul du score (score_latence = exp( -(max(penalise,lat)-penalise)/
        regule )). Défauts 1.0/60.0. Une tâche tolérante peut passer des
        valeurs élevées pour garder les LLM lents compétitifs.
        """
        # Délégation au service LLM Manager (décision centralisée)
        try:
            from services.llm_manager.client import get_llm_client
            client = get_llm_client()
            if client.ping():
                excl_models = list(exclude_models or [])
                if exclude_model:
                    excl_models.append(exclude_model)
                # Idée 18 (O4) : le sorter est choisi selon la tâche. Par défaut
                # best-fallback ; une tâche avec contexte peut choisir le tri
                # (efficiency/cost/thinking...) via la stratégie de la team.
                strategy = "best-fallback"
                if task_context:
                    from services.llm_allocation.sorters import get_sorter
                    sorter = get_sorter(task_context.get("sorter", ""))
                    if sorter:
                        strategy = sorter.name
                res = client.allocate({
                    "strategy": strategy,
                    "task_type": use_case,
                    "min_window": int(min_window or 0),
                    "exclude_providers": list(exclude_providers or [])
                                          + ([exclude_provider] if exclude_provider else []),
                    "exclude_models": excl_models,
                    "agent_id": agent_id or "",
                    "task_context": task_context or {},
                    "features": list(USE_CASE_REQUIREMENTS.get(use_case, {}).get("features", [])),
                    "latence_penalise": latence_penalise,
                    "latence_regule": latence_regule,
                })
                if res.get("status") == "ok" and res.get("provider_ref"):
                    return {"provider_ref": res["provider_ref"],
                            "model_ref": res["model_ref"],
                            "use_case": use_case}
        except Exception:
            pass
        from modules.llm_manager.direct_bridge import DirectBridge

        excl_p = set(exclude_providers or [])
        if exclude_provider:
            excl_p.add(exclude_provider)
        excl_m = set(exclude_models or [])
        if exclude_model:
            excl_m.add(exclude_model)
        # Normalisation : matcher aussi par nom de modèle seul (suffixe après
        # '/') — les callers passent souvent "mimo-v2.5-free" sans préfixe
        # provider (ex. not_same_modele consensus).
        excl_m_suffix = {m.split("/", 1)[-1] for m in excl_m if m and "/" in m}

        bridge = DirectBridge(cat=self.cat, km=self.km)
        req = USE_CASE_REQUIREMENTS.get(use_case, {})
        needs_fc = "function_calling" in req.get("features", [])

        # Modèles en repos (échec runtime) : unavailable ou noretryuntil futur
        # → on les écarte de l'assignation (ne pas retenter un modèle qui vient
        # de rater un appel).
        rest_models: set = set()
        if self.cat:
            try:
                import time as _t
                _now = _t.time()
                rows = self.cat.conn.execute("""
                    SELECT pm.provider_model_name, p.ref AS provider_ref
                    FROM provider_models pm
                    JOIN catalogue_providers p ON p.id = pm.provider_id
                    WHERE pm.noretryuntil > ?
                """, (_now,)).fetchall()
                # Clés (provider, model) en repos — le runtime utilise la ref
                # brute côté provider (provider_model_name), pas la ref préfixée.
                rest_models = {(r["provider_ref"], r["provider_model_name"]) for r in rows}
            except Exception:
                pass

        # Priorité des providers (les plus fiables d'abord)
        provider_priority = ["openai", "nvidia", "groq", "openrouter",
                             "together", "deepinfra", "github-models",
                             "google", "anthropic", "mistral",
                             "huggingface", "ollama"]

        for pref in provider_priority:
            if pref in excl_p:
                continue
            try:
                models = bridge.list_available_models(pref)
            except Exception:
                continue
            if not models:
                continue
            for m in models:
                mref = m["ref"]
                if mref in excl_m or mref.split("/", 1)[-1] in excl_m_suffix \
                        or (pref, mref) in rest_models:
                    continue
                # Vérifier les capacités si possible
                if needs_fc:
                    caps = bridge.get_capabilities(pref, mref)
                    if not caps.supports_function_calling:
                        continue
                # Pas de test réel (probe) : prober consomme une vraie requête
                # API (RPM) — pour un modèle à RPM=1, le probe consomme le slot
                # de la minute et garantit le rate-limit du membre qui suit.
                # Un modèle mort sera marqué unavailable par le premier appel
                # réel (via _mark_call_failed) et le fallback passera au suivant.
                return {"provider_ref": pref, "model_ref": mref,
                        "use_case": use_case}

        return None

    def sync_from_remote(self, force: bool = False) -> int:
        """Synchronise le catalogue depuis l'endpoint central.

        Appelle ``catalogue_remote.refresh_sync()`` avec le catalogue
        et le keymanager de cette instance.

        Args:
            force: Ignore le TTL du cache et force un fetch distant.

        Returns:
            Nombre de modèles synchronisés, 0 si indisponible.
        """
        return catalogue_remote.refresh_sync(self.cat, self.km, force=force)


# ── Seed helpers ──────────────────────────────────────────────

def seed_providers(cat) -> int:
    """Les providers sont désormais seeded directement par le schéma SQL
    (INSERT OR IGNORE dans catalogue_schema.sql). Cette fonction est gardée
    pour rétro-compatibilité mais ne fait plus rien — elle retourne juste
    le count actuel."""
    cur = cat.conn.execute("SELECT COUNT(*) FROM catalogue_providers")
    return cur.fetchone()[0]


def seed_models(cat) -> int:
    import json
    path = _DATA_DIR / "models.json"
    if not path.exists():
        return 0
    with open(path) as f:
        rows = json.load(f)
    return cat.sync_models(rows)


def seed_provider_models(cat) -> int:
    import json
    path = _DATA_DIR / "provider_models.json"
    if not path.exists():
        return 0
    with open(path) as f:
        rows = json.load(f)
    count = 0
    for row in rows:
        cur = cat.conn.execute("SELECT id FROM catalogue_providers WHERE ref = ?", (row["provider_ref"],))
        prow = cur.fetchone()
        cur = cat.conn.execute("SELECT id FROM catalogue_models WHERE ref = ?", (row["model_ref"],))
        mrow = cur.fetchone()
        if not prow or not mrow:
            continue
        cat.conn.execute("""
            INSERT INTO provider_models
                (provider_id, model_id, provider_model_name,
                 context_window_tokens, max_output_tokens,
                 cost_per_input_token, cost_per_output_token,
                 cost_per_thinking_token, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, model_id) DO UPDATE SET
                provider_model_name = excluded.provider_model_name,
                cost_per_input_token = COALESCE(excluded.cost_per_input_token, provider_models.cost_per_input_token),
                cost_per_output_token = COALESCE(excluded.cost_per_output_token, provider_models.cost_per_output_token),
                cost_per_thinking_token = COALESCE(excluded.cost_per_thinking_token, provider_models.cost_per_thinking_token),
                status = excluded.status,
                updated_at = strftime('%s','now')
        """, (prow[0], mrow[0], row.get("provider_model_name", row.get("model_ref")),
              row.get("context_window_tokens"), row.get("max_output_tokens"),
              row.get("cost_per_input_token"), row.get("cost_per_output_token"),
              row.get("cost_per_thinking_token"),
              row.get("status", "active")))
        count += 1
    cat.conn.commit()
    return count
