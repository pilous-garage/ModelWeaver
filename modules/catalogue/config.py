"""
modules/catalogue/config.py - Configuration de la découverte de modèles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DiscoveryConfig:
    """Configuration pour le mécanisme de découverte de modèles."""

    # Source de découverte
    registry_url: str = "https://models.example.com/registry"
    api_key: Optional[str] = None
    timeout: float = 30.0

    # Retry
    max_retry_attempts: int = 3
    retry_base_delay: float = 1.0
    retry_multiplier: float = 2.0
    retry_max_delay: float = 60.0

    # Cache
    cache_ttl: int = 3600
    enable_cache: bool = True

    # Filtres
    allowed_providers: list[str] = field(default_factory=lambda: [
        "openai", "anthropic", "google", "meta", "mistral"
    ])
    min_context_length: int = 4096
    require_modalities: list[str] = field(default_factory=list)

    def to_retry_config(self) -> "RetryConfig":
        """Convertit en RetryConfig."""
        from .retry import RetryConfig
        return RetryConfig(
            max_attempts=self.max_retry_attempts,
            base_delay=self.retry_base_delay,
            multiplier=self.retry_multiplier,
            max_delay=self.retry_max_delay,
        )
