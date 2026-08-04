"""Journal des réponses LLM — trace les échanges complets pour l'analyse.

Écrit dans {home}/log/llm_conversation.log (fichier unique par agent, avec
rotation de taille max). À la différence du FSM log (résumé), ce journal
conserve le CONTENU des réponses LLM et les tool calls, pour permettre des
analyses de boucles / de qualité en profondeur.

Rotation : quand le fichier dépasse `max_bytes` (défaut 10 Mo), il est renommé
en .1, .2 … (max 3 backups), comme un logrotate simple. Le plus ancien est
supprimé.

Usage (depuis autonomous.py) :
    from AgentsCatalogue.lib.llm_conversation_log import log_llm_exchange
    log_llm_exchange(home, p_ref, m_ref, round_n, response, ok=True, error="")
"""

import os
import time
from pathlib import Path
from typing import Any, Optional

MAX_BYTES = 10 * 1024 * 1024  # 10 Mo
MAX_BACKUPS = 3


def _rotate(path: Path) -> None:
    """Rotation style logrotate : .1 → .2 → .3, puis on écrit dans le fichier."""
    if not path.exists():
        return
    if path.stat().st_size < MAX_BYTES:
        return
    # décale les backups existants (le plus vieux tombe)
    for i in range(MAX_BACKUPS, 0, -1):
        src = path.with_suffix(f"{path.suffix}.{i-1}") if i > 1 else path
        dst = path.with_suffix(f"{path.suffix}.{i}")
        if src.exists():
            if dst.exists():
                dst.unlink()
            src.rename(dst)
    # reset le fichier principal (il a été renommé en .1)
    path.touch()


def _safe_str(v: Any, max_len: int = 4000) -> str:
    """Stringifie une valeur, tronquée pour limiter la taille du journal."""
    try:
        s = str(v)
    except Exception:
        s = "<non-serialisable>"
    if len(s) > max_len:
        s = s[:max_len] + f"…<tronqué {len(s) - max_len}o>"
    return s.replace("\r", " ").replace("\n", "⏎")


def _append(home: str, line: str) -> None:
    try:
        log_dir = Path(home) / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "llm_conversation.log"
        _rotate(path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass  # le journal ne doit jamais casser l'agent


def log_llm_exchange(home: str, provider_ref: str, model_ref: str,
                     round_n: int, response: Any = None,
                     ok: bool = True, error: str = "") -> None:
    """Loggue un échange LLM (réponse ou erreur) dans le journal de conversation.

    `response` : ChatResponse (content, tool_calls, finish_reason, usage).
    Le content est conservé en entier (tronqué au max_len pour la taille).
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    header = f"[{ts}] round={round_n} provider={provider_ref} model={model_ref} ok={ok}"
    if not ok:
        _append(home, f"{header} error={_safe_str(error, 500)}")
        return
    if response is None:
        _append(home, f"{header} response=None")
        return
    try:
        content = getattr(response, "content", "") or ""
        tool_calls = getattr(response, "tool_calls", None) or []
        finish = getattr(response, "finish_reason", "") or ""
        usage = getattr(response, "usage", {}) or {}
    except Exception:
        content, tool_calls, finish, usage = "", [], "", {}
    lines = [header + f" finish={finish} usage={usage}"]
    if content:
        lines.append(f"  content: {_safe_str(content)}")
    for tc in tool_calls:
        try:
            fn = tc.get("function", {}).get("name", "?")
            args = tc.get("function", {}).get("arguments", "")
        except Exception:
            fn, args = "?", ""
        lines.append(f"  tool: {fn}({_safe_str(args, 1000)})")
    _append(home, "\n".join(lines))


__all__ = ["log_llm_exchange", "MAX_BYTES"]
