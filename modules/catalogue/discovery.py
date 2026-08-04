"""
modules/catalogue/discovery.py - Découverte de modèles avec retry et logging structuré.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import DiscoveryConfig
from .logger import get_logger
from .retry import (
    RetryConfig,
    RetryExhaustedError,
    execute_with_retry,
)

logger = get_logger("catalogue.discovery")


@dataclass
class ModelInfo:
    """Informations sur un modèle découvert."""

    id: str
    name: str
    provider: str
    context_length: int = 0
    modalities: list[str] = field(default_factory=list)
    pricing: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    discovered_at: float = field(default_factory=time.time)


class ModelDiscovery:
    """
    Moteur de découverte de modèles.

    Interroge un registre de modèles avec retry automatique et backoff
    exponentiel en cas d'échec.
    """

    def __init__(self, config: Optional[DiscoveryConfig] = None) -> None:
        self.config = config or DiscoveryConfig()
        self._cache: dict[str, tuple[list[ModelInfo], float]] = {}
        self._request_id: Optional[str] = None

    def set_request_id(self, request_id: str) -> None:
        """Définit l'identifiant de requête pour le logging."""
        self._request_id = request_id

    def _get_retry_config(self) -> RetryConfig:
        return RetryConfig(
            max_attempts=self.config.max_retry_attempts,
            base_delay=self.config.retry_base_delay,
            multiplier=self.config.retry_multiplier,
            max_delay=self.config.retry_max_delay,
            retryable_exceptions=(ConnectionError, TimeoutError, OSError),
        )

    def _fetch_from_registry(self) -> dict[str, Any]:
        """
        Récupère les modèles depuis le registre (simulé).
        Cette méthode est appelée via execute_with_retry.
        """
        logger.info(
            "fetch_start",
            message="Récupération des modèles depuis le registre",
            registry_url=self.config.registry_url,
        )

        # Simulation : dans un environnement réel, ce serait un appel HTTP
        if not hasattr(self, "_simulate_failure") or not self._simulate_failure:
            return {
                "models": [
                    {
                        "id": "gpt-4o",
                        "name": "GPT-4o",
                        "provider": "openai",
                        "context_length": 128000,
                        "modalities": ["text", "image"],
                        "pricing": {"prompt": 0.005, "completion": 0.015},
                    },
                    {
                        "id": "claude-3-5-sonnet",
                        "name": "Claude 3.5 Sonnet",
                        "provider": "anthropic",
                        "context_length": 200000,
                        "modalities": ["text", "image"],
                        "pricing": {"prompt": 0.003, "completion": 0.015},
                    },
                    {
                        "id": "llama-3.1-70b",
                        "name": "Llama 3.1 70B",
                        "provider": "meta",
                        "context_length": 128000,
                        "modalities": ["text"],
                        "pricing": {"prompt": 0.0, "completion": 0.0},
                    },
                ]
            }

        # Simulation d'échec pour les tests
        raise ConnectionError("Simulated registry connection failure")

    def _parse_models(self, raw_data: dict[str, Any]) -> list[ModelInfo]:
        """Parse les données brutes du registre en objets ModelInfo."""
        models: list[ModelInfo] = []
        for item in raw_data.get("models", []):
            model = ModelInfo(
                id=item["id"],
                name=item["name"],
                provider=item["provider"],
                context_length=item.get("context_length", 0),
                modalities=item.get("modalities", []),
                pricing=item.get("pricing", {}),
                metadata=item.get("metadata", {}),
            )
            models.append(model)
        return models

    def _filter_models(self, models: list[ModelInfo]) -> list[ModelInfo]:
        """Applique les filtres de configuration."""
        filtered = models

        if self.config.allowed_providers:
            filtered = [
                m for m in filtered
                if m.provider in self.config.allowed_providers
            ]

        filtered = [
            m for m in filtered
            if m.context_length >= self.config.min_context_length
        ]

        if self.config.require_modalities:
            filtered = [
                m for m in filtered
                if all(mod in m.modalities for mod in self.config.require_modalities)
            ]

        return filtered

    def discover(self, force_refresh: bool = False) -> list[ModelInfo]:
        """
        Découvre les modèles disponibles avec cache et retry.

        Args:
            force_refresh: Ignore le cache et refetch depuis le registre.

        Returns:
            Liste des modèles découverts et filtrés.
        """
        cache_key = "all"
        now = time.time()

        # Vérification du cache
        if (
            not force_refresh
            and self.config.enable_cache
            and cache_key in self._cache
        ):
            models, cached_at = self._cache[cache_key]
            if now - cached_at < self.config.cache_ttl:
                logger.info(
                    "cache_hit",
                    message=f"Cache valide pour la découverte (âge: {now - cached_at:.1f}s)",
                    cache_age_s=round(now - cached_at, 3),
                )
                return models

        logger.info(
            "discovery_start",
            message="Début de la découverte de modèles",
            force_refresh=force_refresh,
            cache_enabled=self.config.enable_cache,
        )

        try:
            raw_data = execute_with_retry(
                self._fetch_from_registry,
                args=(),
                kwargs={},
                config=self._get_retry_config(),
            )
        except RetryExhaustedError as exc:
            logger.error(
                "discovery_failed",
                message="Tous les essais de connexion au registre ont échoué",
                attempts=exc.attempts,
                error=str(exc.last_exception),
            )
            raise

        models = self._parse_models(raw_data)
        filtered = self._filter_models(models)

        # Mise en cache
        if self.config.enable_cache:
            self._cache[cache_key] = (filtered, now)

        logger.info(
            "discovery_complete",
            message=f"Découverte terminée : {len(filtered)} modèles trouvés",
            total_raw=len(models),
            total_filtered=len(filtered),
            providers=list({m.provider for m in filtered}),
        )

        return filtered

    def get_model(self, model_id: str) -> Optional[ModelInfo]:
        """Récupère un modèle spécifique par son ID."""
        models = self.discover()
        for model in models:
            if model.id == model_id:
                logger.info(
                    "model_found",
                    message=f"Modèle trouvé : {model_id}",
                    model_id=model_id,
                    provider=model.provider,
                )
                return model

        logger.warning(
            "model_not_found",
            message=f"Modèle non trouvé : {model_id}",
            model_id=model_id,
        )
        return None

    def clear_cache(self) -> None:
        """Vide le cache de découverte."""
        self._cache.clear()
        logger.info("cache_cleared", message="Cache de découverte vidé")
