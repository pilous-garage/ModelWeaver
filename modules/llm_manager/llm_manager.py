"""LLM Manager — Catalogue et recommandation de modèles LLM.

Re-exporte les symboles du bridge pour satisfaire le contrat hardcheck.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional

from modules.llm_manager.base_bridge import (
    BaseBridge, ModelCapabilities, ChatResponse, BridgeError, ErrorCategory,
)
from modules.llm_manager.litellm_bridge import LiteLLMBridge

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
        {"ref": "gemini-2.5-flash", "provider": "google", "reason": "Gratuit, très long contexte"},
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
        {"ref": "gemini-2.5-flash", "provider": "google", "reason": "Gratuit, bon raisonnement"},
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
        {"ref": "gemini-2.5-flash", "provider": "google", "reason": "Gratuit, long contexte"},
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
                   max_candidates: int = 8) -> Optional[Dict[str, Any]]:
        """Demande au gestionnaire de LLM une alternative DISPONIBLE et
        différente de ``(exclude_provider, exclude_model)``.

        Parcourt les providers réellement disponibles (clé présente via le
        KeyManager, ou provider local/ollama sans clé) et renvoie le premier
        modèle actif différent. Retourne ``None`` si aucune alternative
        n'est trouvée (ex. un seul LLM configuré).
        """
        excl_p = set(exclude_providers or [])
        if exclude_provider:
            excl_p.add(exclude_provider)
        excl_m = set(exclude_models or [])
        if exclude_model:
            excl_m.add(exclude_model)
        from modules.llm_manager.litellm_bridge import LiteLLMBridge
        from modules.key_manager.key_manager import KeyManager
        from modules.sql.db import ModelWeaverDB
        km = self.km or KeyManager(ModelWeaverDB())
        bridge = LiteLLMBridge(cat=self.cat, km=km)

        # Requête unique : providers avec clé + modèles avec capacités
        req = USE_CASE_REQUIREMENTS.get(use_case, {})
        rf = req.get("features", [])
        needs_fc = "function_calling" in rf
        needs_chat = "chat" in rf

        rows = self.cat.conn.execute("""
            SELECT p.ref as provider_ref, m.ref as model_ref,
                   mc.supports_function_calling, mc.supports_chat,
                   kem.available, kem.last_error
            FROM key_endpoint_models kem
            JOIN catalogue_models m ON m.id = kem.model_id
            JOIN catalogue_providers p ON p.id = kem.provider_id
            LEFT JOIN model_capabilities mc ON mc.model_ref = m.ref
            WHERE kem.available = 1
               OR (kem.available = 0 AND kem.last_checked_at IS NULL)
            ORDER BY kem.available DESC, m.name
        """).fetchall()

        # Trier : modèles de confiance d'abord, puis le reste
        trusted_set = set(TRUSTED_MODELS)
        trusted = []
        others = []
        for r in rows:
            ref = r["model_ref"]
            if ref in excl_m or r["provider_ref"] in excl_p:
                continue
            # Filtrer par capacités
            if needs_fc and r["supports_function_calling"] == 0:
                continue
            if needs_chat and r["supports_chat"] == 0:
                continue
            item = {"provider_ref": r["provider_ref"], "model_ref": ref, "use_case": use_case}
            if ref in trusted_set:
                trusted.append(item)
            else:
                others.append(item)

        candidates = trusted + others

        # Retourner le premier candidat (les capacités sont déjà vérifiées par la requête)
        for c in candidates:
            # Vérification rapide : si model_capabilities a l'info, on l'utilise
            if needs_fc or needs_chat:
                mc = self.cat.conn.execute("""
                    SELECT supports_function_calling, supports_chat
                    FROM model_capabilities WHERE model_ref = ?
                """, (c["model_ref"],)).fetchone()
                if mc:
                    if needs_fc and not mc["supports_function_calling"]:
                        continue
                    if needs_chat and not mc["supports_chat"]:
                        continue
                    return c
            # Fallback health check si capacités inconnues
            try:
                if needs_fc:
                    r = bridge.chat(c["provider_ref"], c["model_ref"],
                        [{"role": "user", "content": "Call test.echo with x=hello"}],
                        tools=[{"type":"function","function":{"name":"test.echo",
                            "parameters":{"type":"object","properties":{"x":{"type":"string"}}}}}],
                        max_tokens=50, temperature=0)
                else:
                    r = bridge.chat(c["provider_ref"], c["model_ref"],
                        [{"role": "user", "content": "ok"}], max_tokens=1)
                if r and (r.content is not None or getattr(r, "tool_calls", None)):
                    return c
            except Exception:
                continue

        return None


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
            INSERT OR IGNORE INTO provider_models
                (provider_id, model_id, provider_model_name,
                 context_window_tokens, max_output_tokens,
                 cost_per_input_token, cost_per_output_token, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (prow[0], mrow[0], row.get("provider_model_name", row.get("model_ref")),
              row.get("context_window_tokens"), row.get("max_output_tokens"),
              row.get("cost_per_input_token"), row.get("cost_per_output_token"),
              row.get("status", "active")))
        count += 1
    cat.conn.commit()
    return count
