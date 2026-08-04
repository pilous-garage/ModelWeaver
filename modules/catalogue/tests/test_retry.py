"""
modules/catalogue/tests/test_retry.py - Tests pour le mécanisme de retry.
"""

from __future__ import annotations

import time

import pytest

from modules.catalogue.retry import (
    RetryConfig,
    RetryExhaustedError,
    execute_with_retry,
    retry_with_backoff,
)


class TestRetryConfig:
    """Tests pour RetryConfig."""

    def test_default_values(self) -> None:
        config = RetryConfig()
        assert config.max_attempts == 3
        assert config.base_delay == 1.0
        assert config.multiplier == 2.0
        assert config.max_delay == 60.0
        assert config.jitter is True
        assert config.jitter_range == 0.25

    def test_custom_values(self) -> None:
        config = RetryConfig(
            max_attempts=5,
            base_delay=0.5,
            multiplier=3.0,
            max_delay=30.0,
            jitter=False,
        )
        assert config.max_attempts == 5
        assert config.base_delay == 0.5
        assert config.multiplier == 3.0
        assert config.max_delay == 30.0
        assert config.jitter is False


class TestComputeDelay:
    """Tests pour le calcul de délai."""

    def test_first_attempt_delay(self) -> None:
        from modules.catalogue.retry import _compute_delay
        delay = _compute_delay(0, RetryConfig(base_delay=1.0, multiplier=2.0, jitter=False))
        assert delay == 1.0

    def test_second_attempt_delay(self) -> None:
        from modules.catalogue.retry import _compute_delay
        delay = _compute_delay(1, RetryConfig(base_delay=1.0, multiplier=2.0, jitter=False))
        assert delay == 2.0

    def test_third_attempt_delay(self) -> None:
        from modules.catalogue.retry import _compute_delay
        delay = _compute_delay(2, RetryConfig(base_delay=1.0, multiplier=2.0, jitter=False))
        assert delay == 4.0

    def test_delay_capped_by_max_delay(self) -> None:
        from modules.catalogue.retry import _compute_delay
        delay = _compute_delay(10, RetryConfig(base_delay=1.0, multiplier=2.0, max_delay=5.0, jitter=False))
        assert delay == 5.0

    def test_jitter_within_range(self) -> None:
        from modules.catalogue.retry import _compute_delay
        for _ in range(20):
            delay = _compute_delay(0, RetryConfig(base_delay=1.0, jitter=True, jitter_range=0.25))
            assert 0.75 <= delay <= 1.25

    def test_delay_never_negative(self) -> None:
        from modules.catalogue.retry import _compute_delay
        delay = _compute_delay(0, RetryConfig(base_delay=0.001, jitter=True, jitter_range=1.0))
        assert delay >= 0.01


class TestExecuteWithRetry:
    """Tests pour execute_with_retry."""

    def test_success_first_try(self) -> None:
        call_count = 0

        def func() -> str:
            nonlocal call_count
            call_count += 1
            return "success"

        result = execute_with_retry(func, args=(), kwargs={}, config=RetryConfig(max_attempts=3))
        assert result == "success"
        assert call_count == 1

    def test_success_after_retries(self) -> None:
        call_count = 0

        def func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("fail")
            return "success"

        result = execute_with_retry(
            func, args=(), kwargs={},
            config=RetryConfig(max_attempts=3, base_delay=0.01, multiplier=1.0, jitter=False),
        )
        assert result == "success"
        assert call_count == 3

    def test_exhausted_retries(self) -> None:
        def func() -> str:
            raise ConnectionError("always fail")

        with pytest.raises(RetryExhaustedError) as exc_info:
            execute_with_retry(
                func, args=(), kwargs={},
                config=RetryConfig(max_attempts=2, base_delay=0.01, jitter=False),
            )
        assert exc_info.value.attempts == 2

    def test_non_retryable_exception_immediate(self) -> None:
        call_count = 0

        def func() -> str:
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        config = RetryConfig(
            max_attempts=3,
            base_delay=0.01,
            retryable_exceptions=(ConnectionError,),
        )
        with pytest.raises(ValueError):
            execute_with_retry(func, args=(), kwargs={}, config=config)
        assert call_count == 1

    def test_with_args_and_kwargs(self) -> None:
        def func(a: int, b: int, multiplier: int = 1) -> int:
            return (a + b) * multiplier

        result = execute_with_retry(
            func, args=(2, 3), kwargs={"multiplier": 2},
            config=RetryConfig(max_attempts=1),
        )
        assert result == 10


class TestRetryWithBackoffDecorator:
    """Tests pour le décorateur retry_with_backoff."""

    def test_decorator_success(self) -> None:
        @retry_with_backoff(RetryConfig(max_attempts=3, base_delay=0.01, jitter=False))
        def my_func() -> str:
            return "ok"

        assert my_func() == "ok"

    def test_decorator_with_retries(self) -> None:
        call_count = 0

        @retry_with_backoff(RetryConfig(max_attempts=3, base_delay=0.01, multiplier=1.0, jitter=False))
        def flaky_func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("transient")
            return "ok"

        assert flaky_func() == "ok"
        assert call_count == 2

    def test_decorator_preserves_function_name(self) -> None:
        @retry_with_backoff(RetryConfig())
        def my_named_func() -> str:
            """My docstring."""
            return "ok"

        assert my_named_func.__name__ == "my_named_func"
