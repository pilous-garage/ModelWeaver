"""
modules/catalogue/logger.py - Logging structuré JSON.

Fournit un logger structuré avec champs standards :
- timestamp ISO-8601
- level
- logger_name
- event
- message
- request_id (si fourni)
- extra (champs additionnels)
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime, timezone
from typing import Any, Optional


class StructuredFormatter(logging.Formatter):
    """Formateur de logs au format JSON structuré."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger_name": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }

        # Champs additionnels (extra)
        standard_attrs = {
            "name", "msg", "args", "created", "relativeCreated",
            "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "pathname", "filename", "module", "levelname", "levelno",
            "msecs", "thread", "threadName", "process", "processName",
            "taskName", "message", "event", "request_id",
        }
        extra_fields = {
            k: v for k, v in record.__dict__.items()
            if k not in standard_attrs and not k.startswith("_")
        }
        if extra_fields:
            log_entry["extra"] = extra_fields

        # Exception
        if record.exc_info and record.exc_info != (None, None, None):
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info),
            }

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class StructuredLogger:
    """Wrapper autour de logging.Logger pour un logging structuré."""

    def __init__(self, name: str = "catalogue") -> None:
        self._logger = logging.getLogger(name)
        self._logger.setLevel(logging.DEBUG)
        self._configured = False

    def _ensure_handler(self) -> None:
        if self._configured:
            return
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(StructuredFormatter())
        self._logger.addHandler(handler)
        self._logger.propagate = False
        self._configured = True

    def _log(
        self,
        level: int,
        event: str,
        message: str = "",
        request_id: Optional[str] = None,
        **extra: Any,
    ) -> None:
        self._ensure_handler()
        extra["event"] = event
        if request_id is not None:
            extra["request_id"] = request_id
        self._logger.log(level, message, extra=extra, stacklevel=3)

    def debug(self, event: str, message: str = "", **kwargs: Any) -> None:
        self._log(logging.DEBUG, event, message, **kwargs)

    def info(self, event: str, message: str = "", **kwargs: Any) -> None:
        self._log(logging.INFO, event, message, **kwargs)

    def warning(self, event: str, message: str = "", **kwargs: Any) -> None:
        self._log(logging.WARNING, event, message, **kwargs)

    def error(self, event: str, message: str = "", **kwargs: Any) -> None:
        self._log(logging.ERROR, event, message, **kwargs)

    def critical(self, event: str, message: str = "", **kwargs: Any) -> None:
        self._log(logging.CRITICAL, event, message, **kwargs)

    def exception(self, event: str, message: str = "", **kwargs: Any) -> None:
        self._ensure_handler()
        extra = {"event": event}
        extra.update(kwargs)
        self._logger.exception(message, extra=extra, stacklevel=3)


# Instance singleton
_logger_instance: Optional[StructuredLogger] = None


def get_logger(name: str = "catalogue") -> StructuredLogger:
    """Retourne l'instance singleton de StructuredLogger."""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = StructuredLogger(name)
    return _logger_instance
