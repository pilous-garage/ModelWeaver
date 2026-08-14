"""Stratégies d'allocation LLM.

Chaque stratégie implémente :
    def allocate(request: AllocationRequest, available: List[ModelOption]) -> Optional[ModelOption]

Une stratégie peut classer, filtrer, ou randomiser la liste available
pour retourner le meilleur candidat selon son critère.
"""

from __future__ import annotations
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class AllocationRequest:
    strategy: str = "random"
    task_type: str = "chat"
    min_window: int = 0
    needs_vision: bool = False
    max_cost_per_call: float = 0.0
    exclude: List[str] = field(default_factory=list)
    agent_name: str = ""
    features: List[str] = field(default_factory=list)
    # Tolérance latence (score_latence = exp( -(max(penalise,lat)-penalise)/regule )).
    # Défauts : penalise=1.0s, regule=60.0s. Une tâche tolérante peut passer
    # penalise/regule élevés pour garder les LLM lents compétitifs.
    latence_penalise: float = 1.0
    latence_regule: float = 60.0


@dataclass
class ModelOption:
    provider_ref: str
    model_ref: str
    provider_model_name: str = ""
    context_window: int = 0
    cost_per_input: float = 0.0
    cost_per_output: float = 0.0
    has_vision: bool = False
    budget_ok: bool = True
    key_available: bool = True
    score: float = 0.0
    score_chat: float = 0.0
    score_coding: float = 0.0
    score_reasoning: float = 0.0
    score_knowledge: float = 0.0
    score_agentic: float = 0.0
    agentic_flag: int = 0        # flag provider_models.agentic (1 = tool calling)
    is_synthetic: int = 0
    runtime_success_count: int = 0
    runtime_calls: int = 0
    runtime_latency_ms: float = 0.0
    # Scores batch (runtime.db) — V0.16 : score de SUCCÈS (1=parfait) + score
    # de latence (0-1, exp) des buckets stables.
    batch_succes: float = 0.0
    batch_latency_score: float = 0.0
    # Score benchmark étiré (score_benchmark_etire) : dans [0.1, 0.9],
    # benchmark croisé par model_key, fallback global si spécialité absente.
    score_etire: float = 0.0

    @property
    def ref(self) -> str:
        return f"{self.provider_ref}/{self.model_ref}"


# ── Registry ────────────────────────────────────────────────────

_STRATEGIES: Dict[str, Callable] = {}


def register_strategy(name: str, fn: Callable):
    _STRATEGIES[name] = fn


def get_strategy(name: str) -> Optional[Callable]:
    return _STRATEGIES.get(name)


def list_strategies() -> List[str]:
    return list(_STRATEGIES.keys())


# ── Random ──────────────────────────────────────────────────────

def _random_allocate(request: AllocationRequest, available: List[ModelOption]) -> Optional[ModelOption]:
    """Choisit au hasard parmi les modèles disponibles avec budget."""
    if not available:
        return None
    return random.choice(available)


register_strategy("random", _random_allocate)


# ── Best-Fallback ───────────────────────────────────────────────

# Modèles connus fiables pour l'agentic (tool calling robuste), utilisés
# quand les scores de benchmark sont absents (score_coding == 0 → tout est
# à 0.5, la sélection devient quasi aléatoire sur 600+ modèles).
_TRUSTED_AGENTIC = [
    "google/gemini-3.5-flash-lite", "google/gemini-3.5-flash",
    "google/gemini-3.6-flash", "google/gemini-3.1-flash-lite",
    "google/gemma-3-27b-it", "google/gemma-3-12b-it",
    "google/gemma-4-26b-a4b-it", "google/gemma-4-31b-it",
    "openai/gpt-5-mini", "openai/gpt-5-nano", "openai/gpt-5",
    "groq/llama-3.3-70b-versatile",
    "nvidia/stepfun-ai/step-3.7-flash",
    "deepseek/deepseek-v3", "deepseek/deepseek-chat",
    "openrouter/openai/gpt-5-mini", "openrouter/anthropic/claude-sonnet-4",
    # Éprouvés en runtime (probes) : rapides + tool calling fiable.
    "nvidia/meta/llama-3.2-11b-vision-instruct", "nvidia/meta/llama-3.3-70b-instruct",
    # Éprouvé en runtime cette nuit (kilo) : 286 appels, 0 erreur. Agentic OK.
    "kilo/poolside/laguna-s-2.1:free", "poolside/laguna-s-2.1:free",
]

# Modèles dont la fenêtre de contexte RÉELLE est trop petite (< ~16k) pour un
# workflow agentic à outils (l'historique des appels outil dépasse vite 8-12k
# tokens, ex. groq/llama-3.1-8b = 8k réels malgré 128k annoncés en BDD).
# On les pénalise lourdement plutôt que de les exclure : si plus rien d'autre
# n'est disponible, la pénalité les laisse en dernier recours.
_SMALL_CONTEXT = [
    "groq/llama-3.1-8b-instant", "groq/llama-3.1-8b-instruct",
    "groq/llama-3.2-3b-preview", "groq/llama-3.2-1b-preview",
    "groq/llama-3.3-3b-versatile",
    "groq/qwen/qwen3.6-27b", "openrouter/qwen/qwen3.6-27b",
    "groq/openai/gpt-oss-20b", "groq/openai/gpt-oss-120b",
    "groq/openai/gpt-oss-safeguard-20b",
    "nvidia/meta/llama-3.1-8b-instruct", "nvidia/google/gemma-2b",
    "openrouter/meta-llama/llama-3.1-8b-instruct",
    "openrouter/openai/gpt-4o-mini-transcribe",
]


def _score_model(option: ModelOption, request: AllocationRequest) -> float:
    """Score composite orienté par type de tâche.

    Utilise le NOUVEAU scoring (validé utilisateur) :
        score_final = score_etire × score_latence × (1 - score_fail_rate)
      - score_etire : benchmark étiré (score_benchmark_etire), croisé par
        model_key, dans [0.1, 0.9]. Baseline 0.1 si jamais benchmarké.
      - score_latence : exp( -(max(penalise, lat_s) - penalise)/regule ),
        lat_s = latence moyenne en secondes (score_batch). Sous penalise →
        1.0 (parfait). Paramètres réglables par requête
        (latence_penalise / latence_regule, défauts 1.0/60.0) : une tâche
        tolérante passe penalise/regule élevés pour garder les LLM lents.
      - score_fail_rate : composite pondéré (0.4/0.3/0.2/0.1 des fenêtres
        5m/1h/1j/1w) depuis score_batch. 0 si aucun appel (pas d'échec).

    Quand les scores batch sont absents (modèle jamais appelé), on retombe sur
    les pénalités runtime classiques (fail_rate lissé Laplace + latence
    linéaire) pour ne pas laisser un inconnu à score plein.
    """
    import math
    req_features = set(getattr(request, "features", None) or [])

    # ── Composante benchmark étiré (0.1-0.9) ──
    score = option.score_etire if option.score_etire > 0 else 0.1

    # Bonus fiabilité éprouvée : un modèle benchmarké ET fiable (batch)
    # conserve un avantage — le benchmark étiré est déjà relatif.
    # (Le fallback global est déjà intégré par score_benchmark_etire.)

    # ── Composante latence (exponentielle paramétrable) ──
    # V0.16 : score de latence précalculé (0-1) des buckets stables, sinon
    # exp de la latence runtime moyenne.
    if option.batch_latency_score > 0:
        score *= option.batch_latency_score
    elif option.batch_latency_ms > 0 and option.runtime_calls > 0:
        lat_s = max(request.latence_penalise, option.batch_latency_ms / 1000.0)
        score_latence = math.exp(
            -(lat_s - request.latence_penalise) / request.latence_regule)
        score *= score_latence

    # ── Composante succès (V0.16) ──
    # Score de SUCCÈS des buckets stables (1 = parfait, jamais appelé → 1.0
    # neutre) — multiplié directement. Fallback runtime lissé Laplace.
    if option.batch_succes > 0:
        score *= option.batch_succes
    elif option.runtime_calls >= 1:
        smoothed = (1.0 + option.runtime_success_count) / (1.0 + option.runtime_calls)
        score *= smoothed

    # ── Pénalité modèles JAMAIS appelés ──
    # Un modèle sans AUCUN appel runtime (runtime_calls=0) est inconnu : son
    # benchmark peut être haut mais il peut être mort (huggingface Not Found,
    # cohere tool unsupported…). On le pénalise pour favoriser les modèles
    # ÉPROUVÉS (qui ont déjà réussi). Sans ça, le scoring re-sélectionne en
    # boucle des modèles morts jamais testés avant de trouver un modèle fiable.
    if option.runtime_calls == 0:
        score *= 0.4
    elif option.runtime_success_count > 0:
        # Bonus modeste pour un modèle fiable (≥1 succès réel).
        score *= 1.05

    # ── Ajustements par tâche / contexte / coût (additifs) ──
    # Pénalité si pas assez de window
    if request.min_window > 0 and option.context_window > 0:
        ratio = min(option.context_window / request.min_window, 2.0)
        score += 0.1 * (ratio / 2.0)

    # Bonus si coût bas
    cost = option.cost_per_input + option.cost_per_output
    if cost > 0:
        cost_factor = max(0, 1.0 - (cost / 1e-5))
        score += 0.05 * cost_factor

    # Pénalité si vision nécessaire mais absente
    if request.needs_vision and not option.has_vision:
        score -= 0.2

    # Pénalité si fenêtre trop petite pour la tâche demandée (workflow agentic
    # à outils : l'historique dépasse vite la fenêtre réelle du modèle)
    if request.min_window > 0 and option.context_window > 0:
        if option.context_window < request.min_window:
            score -= 0.5
    if option.ref in _SMALL_CONTEXT:
        score -= 0.4
    # Fenêtre INCONNUE (0) + historique gros requis : risqué (le modèle peut
    # avoir une fenêtre réelle trop petite, ex. groq/qwen3.6-27b ~32k).
    # Pénalité modérée : on préfère un modèle à fenêtre connue.
    if request.min_window > 0 and option.context_window == 0:
        score -= 0.2

    # Pénalité si score synthétique (moins fiable) — sauf pour les modèles
    # connus fiables (le flag synthétique est souvent posé pour les modèles
    # récents sans benchmark solide, pas un vrai signal de faible qualité).
    if option.is_synthetic and option.ref not in _TRUSTED_AGENTIC:
        score *= 0.8

    # Pénalité si PAS agentic (tool calling) pour une tâche qui en exige :
    # un modèle agentic=0 (ex. cohere command-r) répond en texte sans jamais
    # appeler les outils → l'agent boucle en "no_action". Seuls les modèles
    # agentic=1 (nvidia/gpt-oss-20b, groq/llama-3.3, google…) font du tool
    # calling. Pénalité forte pour que les tâches coding/agentic retombent sur
    # un modèle qui SAIT utiliser les outils.
    if ("function_calling" in req_features and option.agentic_flag == 0):
        score *= 0.25

    return max(0.0, min(1.0, score))


def _best_fallback_allocate(request: AllocationRequest, available: List[ModelOption]) -> Optional[ModelOption]:
    """Meilleur score, avec fallback : retourne la liste triée.

    La route llm/allocate peut réessayer avec le suivant si le premier échoue.

    Rotation anti-rate-limit : parmi les modèles à score quasi-égal (écart
    TRÈS serré ≤ 0.01), on tire au hasard au lieu de toujours prendre le #1.
    Sinon les 9 membres du swarm reçoivent tous le même modèle → son RPM
    explose au 3e membre. Le tirage répartit le quota entre gemini/gemma du
    même provider. La fenêtre est volontairement étroite pour ne PAS inclure
    des modèles morts non testés (score 0.5) dans la rotation des bons (0.75).
    """
    if not available:
        return None
    scored = [(m, _score_model(m, request)) for m in available]
    scored.sort(key=lambda x: x[1], reverse=True)
    top_score = scored[0][1]
    # Top modèles à score quasi-égal — fenêtre étroite pour rester sur les
    # modèles réellement au niveau du meilleur.
    top = [m for m, s in scored if s >= top_score - 0.01]
    best = random.choice(top) if len(top) > 1 else top[0]
    best.score = top_score
    return best


register_strategy("best-fallback", _best_fallback_allocate)


# ── Eco (TODO) ──────────────────────────────────────────────────

def _eco_allocate(request: AllocationRequest, available: List[ModelOption]) -> Optional[ModelOption]:
    """Moindre coût : choisit le modèle le moins cher par token."""
    if not available:
        return None
    best = min(available, key=lambda m: m.cost_per_input + m.cost_per_output)
    best.score = 1.0 - (best.cost_per_input + best.cost_per_output)
    return best


register_strategy("eco", _eco_allocate)


# ── Fast (TODO) ─────────────────────────────────────────────────

def _fast_allocate(request: AllocationRequest, available: List[ModelOption]) -> Optional[ModelOption]:
    """Faible latence : se base sur le score de latence.

    Pour l'instant, préfère les petits modèles (proxy latence).
    À terme, utilisera model_scores.latency_score.
    """
    if not available:
        return None
    triés = sorted(available, key=lambda m: m.cost_per_input + m.cost_per_output)
    best = triés[0]
    best.score = 1.0
    return best


register_strategy("fast", _fast_allocate)
