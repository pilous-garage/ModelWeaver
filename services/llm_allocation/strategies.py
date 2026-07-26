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

    # Pénalité si score synthétique (moins fiable)
    if option.is_synthetic:
        score *= 0.8

    return max(0.0, min(1.0, score))


def _best_fallback_allocate(request: AllocationRequest, available: List[ModelOption]) -> Optional[ModelOption]:
    """Meilleur score, avec fallback : retourne la liste triée.

    La route llm/allocate peut réessayer avec le suivant si le premier échoue.
    """
    if not available:
        return None
    scored = [(m, _score_model(m, request)) for m in available]
    scored.sort(key=lambda x: x[1], reverse=True)
    best = scored[0][0]
    best.score = scored[0][1]
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
