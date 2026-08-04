"""
modules/catalogue/tests/test_logger.py - Tests pour le logging structuré.
"""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from modules.catalogue.logger import StructuredFormatter, StructuredLogger, get_logger


class TestStructuredFormatter:
    """Tests pour StructuredFormatter."""

    def test_json_output(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Hello %s",
            args=("world",),
            exc_info=None,
        )
        record.event = "test_event"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["event"] == "test_event"
        assert parsed["message"] == "Hello world"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed

    def test_extra_fields(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        record.event = "test"
        record.custom_field = "custom_value"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["extra"]["custom_field"] == "custom_value"

    def test_request_id(self) -> None:
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        record.event = "test"
        record.request_id = "req-123"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["request_id"] == "req-123"

    def test_exception_included(self) -> None:
        formatter = StructuredFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="error occurred",
            args=(),
            exc_info=exc_info,
        )
        record.event = "error_event"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert parsed["exception"]["type"] == "ValueError"
        assert parsed["exception"]["message"] == "test error"


class TestStructuredLogger:
    """Tests pour StructuredLogger."""

    def test_info_log(self, capsys: pytest.CaptureFixture) -> None:
        logger = StructuredLogger("test_info")
        logger.info("test_event", "Test message", key="value")
        captured = capsys.readouterr()
        lines = [l for l in captured.out.strip().split("\n") if l]
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["event"] == "test_event"
        assert parsed["message"] == "Test message"
        assert parsed["level"] == "INFO"
        assert parsed["extra"]["key"] == "value"

    def test_warning_log(self, capsys: pytest.CaptureFixture) -> None:
        logger = StructuredLogger("test_warning")
        logger.warning("warn_event", "Warning message")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["level"] == "WARNING"
        assert parsed["event"] == "warn_event"

    def test_error_log(self, capsys: pytest.CaptureFixture) -> None:
        logger = StructuredLogger("test_error")
        logger.error("error_event", "Error message")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["level"] == "ERROR"

    def test_debug_log(self, capsys: pytest.CaptureFixture) -> None:
        logger = StructuredLogger("test_debug")
        logger.debug("debug_event", "Debug message")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["level"] == "DEBUG"

    def test_critical_log(self, capsys: pytest.CaptureFixture) -> None:
        logger = StructuredLogger("test_critical")
        logger.critical("crit_event", "Critical message")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["level"] == "CRITICAL"

    def test_exception_log(self, capsys: pytest.CaptureFixture) -> None:
        logger = StructuredLogger("test_exception")
        try:
            raise RuntimeError("test exception")
        except RuntimeError:
            logger.exception("exception_event", "An exception occurred")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["level"] == "ERROR"
        assert "exception" in parsed
        assert parsed["exception"]["type"] == "RuntimeError"

    def test_request_id_in_log(self, capsys: pytest.CaptureFixture) -> None:
        logger = StructuredLogger("test_req")
        logger.info("event", "msg", request_id="req-abc")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["request_id"] == "req-abc"

    def test_singleton_get_logger(self) -> None:
        logger1 = get_logger("singleton_test")
        logger2 = get_logger("singleton_test")
        assert logger1 is logger2


class TestLoggerIntegration:
    """Tests d'intégration du logger."""

    def test_multiple_logs_order(self, capsys: pytest.CaptureFixture) -> None:
        logger = StructuredLogger("test_order")
        logger.info("first", "First message")
        logger.warning("second", "Second message")
        logger.error("third", "Third message")
        captured = capsys.readouterr()
        lines = [l for l in captured.out.strip().split("\n") if l]
        events = [json.loads(l)["event"] for l in lines]
        assert events == ["first", "second", "third"]

    def test_empty_message(self, capsys: pytest.CaptureFixture) -> None:
        logger = StructuredLogger("test_empty")
        logger.info("empty_event", "")
        captured = capsys.readouterr()
        parsed = json.loads(captured.out.strip())
        assert parsed["message"] == ""
        assert parsed["event"] == "empty_event"
