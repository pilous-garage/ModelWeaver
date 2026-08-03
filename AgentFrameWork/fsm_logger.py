"""Logger FSM par agent — trace chaque étape du workflow dans un fichier.

Le FSM génère un fichier de log par exécution : `{home}/log/fsm_{timestamp}.log`.
Chaque ligne append (flush direct) au format :

    ts|level|kind|message

kinds possibles :
  - fsm/start, fsm/step, fsm/end     : transitions de la machine d'états
  - llm/call, llm/ok, llm/error      : appels LLM (provider, model, prompt court)
  - tool/call, tool/ok, tool/error   : tool calls / skills exécutés
  - agent/call, agent/ok             : appels inter-agents (agent_call)

Niveaux (config de l'agent, défaut debug) :
  - debug : tout est loggé
  - info  : steps + llm + tools (sans les arguments détaillés)
  - warn  : seulement les échecs / erreurs

Le niveau est lu depuis la config de l'agent : `config.log_level` (ou
`llm.log_level`). Best-effort : un échec de log ne casse jamais l'agent.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
DEFAULT_LEVEL = "debug"


def _trunc(s: str, max_len: int = 120) -> str:
    """Tronque une valeur pour garder le log lisible."""
    s = str(s).replace("\n", " ").replace("|", "/").strip()
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


class FSMLogger:
    """Logger append-only d'une exécution FSM.

    Usage :
        logger = FSMLogger(home, agent_name="coder-a", level="debug")
        logger.log("info", "fsm/step", "id=start type=call")
        logger.close()
    """

    def __init__(self, home: str, agent_name: str = "",
                 level: str = DEFAULT_LEVEL):
        self.level = LEVELS.get((level or "").lower(), LEVELS[DEFAULT_LEVEL])
        self._path: Optional[Path] = None
        self._fh = None
        try:
            log_dir = Path(home) / "log"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            self._path = log_dir / f"fsm_{ts}.log"
            self._fh = open(self._path, "a", encoding="utf-8")
        except Exception:
            self._fh = None

    @property
    def path(self) -> Optional[str]:
        return str(self._path) if self._path else None

    @classmethod
    def attach(cls, home: str, level: str = DEFAULT_LEVEL) -> "FSMLogger":
        """Ouvre le fichier de log FSM le plus RÉCENT du home (même run).

        Utilisé par les skills (ex. workflow/autonomous@v1) qui s'exécutent
        DANS un step du FSM : ils appendent au même log que le run parent
        (celui créé par AgentManager). S'il n'y a pas de fichier récent
        (< 30 min), en crée un nouveau.
        """
        logger = cls(home, level=level)
        try:
            log_dir = Path(home) / "log"
            if log_dir.is_dir():
                files = sorted(log_dir.glob("fsm_*.log"))
                if files:
                    newest = files[-1]
                    now = time.time()
                    if now - newest.stat().st_mtime < 1800:
                        # Réutiliser le fichier le plus récent (même run)
                        logger._path = newest
                        try:
                            if logger._fh:
                                logger._fh.close()
                        except Exception:
                            pass
                        logger._fh = open(newest, "a", encoding="utf-8")
        except Exception:
            pass
        return logger

    def enabled(self, level: str) -> bool:
        return LEVELS.get(level, 10) >= self.level

    def log(self, level: str, kind: str, message: str) -> None:
        """Ajoute une ligne si le niveau est suffisant. Ne lève jamais."""
        if not self._fh:
            return
        if LEVELS.get(level, 10) < self.level:
            return
        try:
            ts = time.strftime("%H:%M:%S") + f".{int(time.time() * 1000) % 1000:03d}"
            self._fh.write(f"{ts}|{level}|{kind}|{_trunc(message)}\n")
            self._fh.flush()
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._fh:
                self._fh.close()
                self._fh = None
        except Exception:
            self._fh = None

    def __del__(self) -> None:
        self.close()
