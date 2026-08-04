"""Module catalogue — discovery, pricing, sync."""

import logging

logger = logging.getLogger("modelweaver.catalogue")

# #43 (human-choice) : logging structuré + retry backoff + métriques pour la
# découverte de modèles. Modules ajoutés par le swarm (issue #43).
from .config import DiscoveryConfig  # noqa: E402
from .logger import get_logger, StructuredLogger  # noqa: E402
from .retry import retry_with_backoff, RetryConfig  # noqa: E402
from .discovery import ModelDiscovery  # noqa: E402

__all__ = [
    "get_logger",
    "StructuredLogger",
    "retry_with_backoff",
    "RetryConfig",
    "ModelDiscovery",
    "DiscoveryConfig",
]
