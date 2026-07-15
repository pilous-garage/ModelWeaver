"""Logging structuré JSON — remplace les print() dans les services.

Usage:
    from services.logger import MWLogger
    log = MWLogger("daemon")
    log.info("Daemon démarré", port=8775, version="0.6.11")
    log.warning("Bind échoué", port=8775, error=str(e))

Output (JSON lignes) :
    {"ts": "2026-07-15T12:00:00Z", "level": "INFO", "service": "daemon",
     "msg": "Daemon démarré", "port": 8775, "version": "0.6.11"}
"""

import json
import logging
import logging.handlers
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Dict

from services._common import mw_home


class JSONFormatter(logging.Formatter):
    """Formateur JSON pour logging structuré."""

    def format(self, record: logging.LogRecord) -> str:
        entry: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "?"),
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            entry["exc"] = self.formatException(record.exc_info)
        for k, v in getattr(record, "extra", {}).items():
            entry[k] = v
        return json.dumps(entry, default=str)


# ── Initialisation unique du root logger "mw" ──

_ROOT_INIT_LOCK = threading.Lock()
_ROOT_INITIALIZED = False


def _ensure_root():
    global _ROOT_INITIALIZED
    if _ROOT_INITIALIZED:
        return
    with _ROOT_INIT_LOCK:
        if _ROOT_INITIALIZED:
            return
        log_dir = mw_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = str(log_dir / "modelweaver.log")

        root = logging.getLogger("mw")
        root.setLevel(logging.DEBUG)
        root.propagate = False

        fh = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10_485_760, backupCount=5)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(JSONFormatter())
        root.addHandler(fh)

        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.WARNING)
        sh.setFormatter(JSONFormatter())
        root.addHandler(sh)

        _ROOT_INITIALIZED = True


class MWLogger:
    """Logger structuré. Retourne un enfant du root logger 'mw'."""

    def __init__(self, service: str):
        _ensure_root()
        self._service = service
        self._logger = logging.getLogger(f"mw.{service}")

    def _log(self, level: int, msg: str, **kwargs):
        exc_info = kwargs.pop("exc_info", None)
        extra = {"extra": kwargs} if kwargs else {}
        extra["service"] = self._service
        self._logger.log(level, msg, extra=extra, exc_info=exc_info)
        for h in self._logger.handlers:
            h.flush()
        # Root handlers aussi
        for h in logging.getLogger("mw").handlers:
            h.flush()

    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs):
        self._log(logging.CRITICAL, msg, **kwargs)
