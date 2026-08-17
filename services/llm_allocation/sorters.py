"""llm_allocation/sorters — FONCTIONS DE TRI PERSONNALISABLES (Idée 18, O4).

Interface publique : toute stratégie de tri est une SOUS-CLASSE de SorterBase
qui implémente :
    key(option: ModelOption, request: AllocationRequest) -> float
    sort(request, options) -> List[ModelOption]   (défaut : tri desc par key)

Le registry `SORTERS` permet de choisir le tri par nom (depuis le manifest /
la stratégie de team). Pour personnaliser, on SURSOLICITE SorterBase et on
l'enregistre — l'héritage donne les "fonctions cachées" (les helpers de
score/coût/efficacité réutilisables) sans réécrire le tri.

Le DÉFAUT reste best-fallback (strategies.py) — ces sorters élargissent le
choix pour l'allocation évolutive (budget par pipeline, niveaux, thinking_power).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from services.llm_allocation.strategies import AllocationRequest, ModelOption


class SorterBase:
    """Base du tri. Surcharger `key` (plus le retour est grand, mieux c'est)."""

    name: str = "sorter"

    def key(self, option: ModelOption, request: AllocationRequest) -> float:
        raise NotImplementedError

    def sort(self, request: AllocationRequest,
             options: List[ModelOption]) -> List[ModelOption]:
        return sorted(options, key=lambda o: self.key(o, request), reverse=True)

    def pick(self, request: AllocationRequest,
             options: List[ModelOption]) -> Optional[ModelOption]:
        ordered = self.sort(request, options)
        return ordered[0] if ordered else None

    # ── Helpers réutilisables ("fonctions cachées" de l'héritage) ──
    @staticmethod
    def _cost(option: ModelOption) -> float:
        """Coût par token (entrée + sortie) de l'option."""
        return (option.cost_per_input or 0.0) + (option.cost_per_output or 0.0)

    @staticmethod
    def _latency_s(option: ModelOption) -> float:
        """Latence moyenne réelle en secondes (fallback 5s si jamais appelé)."""
        if option.runtime_calls > 0:
            return (option.runtime_latency_ms or 0.0) / 1000.0
        return 5.0

    @staticmethod
    def _succes_rate(option: ModelOption) -> float:
        """Taux de succès réel (Laplace, neutre 1.0 si jamais appelé)."""
        if option.runtime_calls >= 1:
            return (1.0 + option.runtime_success_count) / (1.0 + option.runtime_calls)
        return 1.0

    @staticmethod
    def _competence(option: ModelOption) -> float:
        """Compétence benchmark étirée du modèle (score_etire, fallback 0.1)."""
        return option.score_etire if option.score_etire > 0 else 0.1


class ScoreSorter(SorterBase):
    """Tri par score composite hérité de _score_model (strategies.py)."""

    name = "score"

    def key(self, option: ModelOption, request: AllocationRequest) -> float:
        from services.llm_allocation.strategies import _score_model
        return _score_model(option, request)


class CostSorter(SorterBase):
    """Moins cher d'abord (coût par token), latence et succès en départage."""

    name = "cost"

    def key(self, option: ModelOption, request: AllocationRequest) -> float:
        c = self._cost(option)
        if c <= 0:
            # Gratuit : très bon signal économique (ordre de priorité élevé).
            c = 1e-9
        return 1.0 / (c + self._latency_s(option) * 1e-6)  # max = meilleur


class EfficiencySorter(SorterBase):
    """Meilleur RATIO compétence / coût (optimise qualité par unité d'argent)."""

    name = "efficiency"

    def key(self, option: ModelOption, request: AllocationRequest) -> float:
        c = self._cost(option)
        comp = self._competence(option)
        if c <= 0:
            return comp * 1e6  # gratuit → quasi max (compétence seule)
        return comp / (c + 1e-9)


class ThinkingPowerSorter(SorterBase):
    """Meilleur thinking_power d'abord (pour les tâches à fort raisonnement).

    Le thinking_power vient de la table dérivée (scoreur) — à injecter via
    request.features ou résolu par model_id au besoin."""

    name = "thinking"

    def key(self, option: ModelOption, request: AllocationRequest) -> float:
        tp = float(getattr(option, "thinking_power", 0.0) or 0.0)
        if tp <= 0:
            return self._competence(option)  # fallback compétence
        return tp


class WeightedSorter(SorterBase):
    """Tri pondéré custom : poids sur (compétence, coût, latence, succès).

    Ex. : quality=0.6, cost=0.25, latency=0.15 → privilégie la qualité tout en
    pénalisant le coût et la latence. Les poids se normalisent sur leur somme.
    La base la plus souple pour une stratégie métier personnalisée.
    """

    name = "weighted"
    w_quality: float = 0.5
    w_cost: float = 0.3
    w_latency: float = 0.1
    w_succes: float = 0.1

    def key(self, option: ModelOption, request: AllocationRequest) -> float:
        total_w = self.w_quality + self.w_cost + self.w_latency + self.w_succes
        if total_w <= 0:
            return self._competence(option)
        # Coût et latence : plus bas = mieux → on retourne leur inverse borné.
        c = max(self._cost(option), 1e-9)
        lat = max(self._latency_s(option), 1e-3)
        score = (self.w_quality * self._competence(option)
                 + self.w_cost / c
                 + self.w_latency / lat
                 + self.w_succes * self._succes_rate(option))
        return score / total_w


SORTERS: Dict[str, Callable] = {}


def register_sorter(sorter_cls):
    SORTERS[sorter_cls.name] = sorter_cls
    return sorter_cls


def get_sorter(name: str) -> Optional[SorterBase]:
    cls = SORTERS.get(name)
    return cls() if cls else None


def list_sorters() -> List[str]:
    return list(SORTERS.keys())


# Enregistrement des sorters par défaut.
register_sorter(ScoreSorter)
register_sorter(CostSorter)
register_sorter(EfficiencySorter)
register_sorter(ThinkingPowerSorter)
register_sorter(WeightedSorter)