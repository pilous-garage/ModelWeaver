"""
modules/catalogue/retry.py - Retry avec backoff exponentiel.

Implémente un mécanisme de retry configurable :
- backoff exponentiel (base * multiplier ^ attempt)
- backoff plafonné (max_delay)
- jitter optionnel (±25% par défaut)
- exceptions spécifiques à intercepter
- callback optionnel après chaque échec
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Type, TypeVar

from .logger import get_logger

logger = get_logger("catalogue.retry")

T = TypeVar("T")


@dataclass
class RetryConfig:
    """Configuration du mécanisme de retry."""

    max_attempts: int = 3
    base_delay: float = 1.0
    multiplier: float = 2.0
    max_delay: float = 60.0
    jitter: bool = True
    jitter_range: float = 0.25
    retryable_exceptions: tuple[Type[Exception], ...] = (Exception,)
    on_retry: Optional[Callable[[int, Exception, float], None]] = None


class RetryExhaustedError(Exception):
    """Levée lorsque tous les essais de retry sont épuisés."""

    def __init__(self, attempts: int, last_exception: Exception) -> None:
        self.attempts = attempts
        self.last_exception = last_exception
        super().__init__(
            f"Retry exhausted after {attempts} attempts. "
            f"Last error: {last_exception}"
        )


def _compute_delay(attempt: int, config: RetryConfig) -> float:
    """Calcule le délai avant le prochain essai avec backoff exponentiel."""
    delay = config.base_delay * (config.multiplier ** attempt)
    delay = min(delay, config.max_delay)

    if config.jitter:
        jitter_amount = delay * config.jitter_range
        delay = delay + random.uniform(-jitter_amount, jitter_amount)
        delay = max(0.01, delay)

    return delay


def retry_with_backoff(config: Optional[RetryConfig] = None):
    """
    Décorateur pour ajouter un retry avec backoff exponentiel à une fonction.

    Usage:
        @retry_with_backoff(RetryConfig(max_attempts=5))
        def discover_models():
            ...
    """
    if config is None:
        config = RetryConfig()

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return execute_with_retry(func, args, kwargs, config)

        return wrapper

    return decorator


def execute_with_retry(
    func: Callable[..., T],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    config: RetryConfig,
) -> T:
    """Exécute une fonction avec retry et backoff exponentiel."""
    last_exception: Optional[Exception] = None
    func_name = f"{func.__module__}.{func.__qualname__}"

    for attempt in range(config.max_attempts):
        try:
            return func(*args, **kwargs)
        except config.retryable_exceptions as exc:
            last_exception = exc
            delay = _compute_delay(attempt, config)

            logger.warning(
                "retry_attempt",
                message=f"Attempt {attempt + 1}/{config.max_attempts} failed for {func_name}",
                function=func_name,
                attempt=attempt + 1,
                max_attempts=config.max_attempts,
                error_type=type(exc).__name__,
                error_message=str(exc),
                next_delay_s=round(delay, 3),
            )

            if config.on_retry is not None:
                config.on_retry(attempt + 1, exc, delay)

            if attempt < config.max_attempts - 1:
                time.sleep(delay)

    raise RetryExhaustedError(config.max_attempts, last_exception)  # type: ignore[arg-type]
