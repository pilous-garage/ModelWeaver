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
    is_synthetic: int = 0
    runtime_success_count: int = 0
    runtime_calls: int = 0
    runtime_latency_ms: float = 0.0

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

    Utilise le score spécifique à la tâche quand disponible (issu du
    benchmark), sinon le score qualité global. Pondère aussi le coût,
    la fenêtre de contexte et la compatibilité vision.
    """
    TASK_SCORE_MAP = {
        "chat": "score_chat",
        "knowledge": "score_knowledge",
        "coding": "score_coding",
        "reasoning": "score_reasoning",
        "agentic": "score_agentic",
        "analysis": "score_reasoning",
        "writing": "score_chat",
    }

    score = 0.5  # baseline

    # Score par tâche (prioritaire) ou fallback global
    task_key = TASK_SCORE_MAP.get(request.task_type, "score_chat")
    task_score = getattr(option, task_key, 0.0)
    if task_score > 0:
        score = task_score / 100.0
        # Un modèle connu fiable (listé _TRUSTED_AGENTIC) garde un plancher :
        # les scores benchmark partiels/bas d'un modèle récent (ex.
        # google/gemini-3.5-flash avec score_coding=5.56) ne doivent pas le
        # reléguer derrière des inconnus à 0.5 aléatoire.
        if option.ref in _TRUSTED_AGENTIC:
            score = max(score, 0.5 + 0.25)
    elif option.ref in _TRUSTED_AGENTIC:
        # Pas de benchmark : favoriser les modèles connus fiables en agentic
        score += 0.25

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

    # ── Métriques runtime (fenêtre glissante model_call_log) ──
    # Calcul DÉTERMINISTE et continu (pas de paliers) :
    #   score -= fail_rate                      (0→1 : 0 échec → 1 tout échoue)
    #   score -= latence_secondes / 100         (latence_ms / 100000)
    # Pénalité dès le 1er appel : un modèle qui échoue même UNE fois (404,
    # auth, quota) est à déprioritiser — le laisser à score plein = il est
    # re-testé à chaque run (repos court expiré → round-robin le re-choisit).
    # Un appel réussi seul ne pénalise pas (fail_rate = 0).
    # Taux de succès LISSÉ (Laplace) pour éviter les extrêmes 0/1 et 1/1 :
    #   p = (1 + succès) / (1 + total)
    # Un seul succès (1/1) → p=1, fail=0 ; un seul échec (0/1) → p=0.5,
    # fail=0.5 (au lieu de 1.0 brut qui éliminerait à tort un modèle qui
    # a échoué une seule fois sur un aléa).
    if option.runtime_calls >= 1:
        smoothed = (1.0 + option.runtime_success_count) / (1.0 + option.runtime_calls)
        fail_rate = 1.0 - smoothed
        score -= fail_rate
        # Pénalité latence : linéaire, règle utilisateur — 1 minute de latence
        # = -0.3 pt (score max = 1). Donc latency_ms / 60000 × 0.3 = /200000.
        #   0.5s → -0.0025 ; 1s → -0.005 ; 2s → -0.01 ; 5s → -0.025
        #   10s → -0.05 ; 30s → -0.15 ; 60s → -0.30
        score -= option.runtime_latency_ms / 200000.0
        # Bonus de FIABILITÉ éprouvée : un modèle testé avec un bon taux de
        # succès passe DEVANT un modèle jamais testé (inconnu = risque de
        # modèle mort, deprecated, 404…). Sans ça, les non-testés à score
        # benchmark 0.75 (souvent morts) passent devant le champion 46/48.
        if smoothed >= 0.8 and option.runtime_calls >= 3:
            score += 0.2
    else:
        # Modèle JAMAIS testé : inconnu = risqué (404, deprecated, pas de
        # crédit). On le pénalise légèrement pour que les modèles éprouvés
        # (ex. nvidia 88% sur 300+ appels) soient choisis avant en fallback.
        # Ce n'est pas un blocage : si tout est mort, il reste allouable.
        score -= 0.05

    return min(1.0, score)


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
