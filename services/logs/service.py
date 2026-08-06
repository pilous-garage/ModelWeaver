"""services/logs — service de journaux génériques.

Chaque "canal" est un fichier `~/.modelweaver/logs/<name>.log` (append-only).
Permet à n'importe quel module/frontend de logger simplement, puis de relire,
de suivre (tail), de lister et de vider.

Utilisation (route daemon) :
  POST /v1/log/<name>/write  {level, message, events?}   → append
  POST /v1/log/<name>/get    {max_lines?}                → contenu complet
  POST /v1/log/<name>/tail   {lines?}                    → dernières N lignes
  POST /v1/log/<name>/list                                → métas (taille, lignes)
  POST /v1/log/<name>/clear                               → vide le fichier
  POST /v1/log/list                                       → tous les canaux
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from services._common import mw_home

LOGS_DIR = mw_home() / "logs"
MAX_FILE_MB = 50  # rotation au-delà (50 Mo)
MAX_READ_LINES = 50_000


def _ensure_dir() -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR


def _path(name: str) -> Path:
    # sécurise le nom : pas de slash / montée de répertoire
    safe = Path(name).name
    return _ensure_dir() / f"{safe}.log"


def list_channels() -> List[Dict[str, Any]]:
    out = []
    for f in sorted(_ensure_dir().glob("*.log")):
        try:
            size = f.stat().st_size
            lines = sum(1 for _ in f.open(encoding="utf-8", errors="replace"))
            out.append({"name": f.stem, "path": str(f), "size": size, "lines": lines})
        except Exception:
            continue
    return out


def write(name: str, level: str = "INFO", message: str = "",
          events: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Append des lignes au canal. `events` = liste d'événements JSON (tracing)."""
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    _maybe_rotate(p)
    with p.open("a", encoding="utf-8") as f:
        n = 0
        if message:
            line = f"{datetime.now().isoformat()} [{level}] {message}\n"
            f.write(line)
            n += 1
        if events:
            for ev in events:
                ts = ev.get("ts", datetime.now().isoformat())
                line = f"{ts} [{ev.get('level', level)}] {ev.get('type', '?')} {json.dumps(ev.get('detail', ''), ensure_ascii=False)}\n"
                f.write(line)
                n += 1
    return {"status": "ok", "channel": name, "written": n}


def get(name: str, max_lines: Optional[int] = None) -> Dict[str, Any]:
    """Retourne le contenu complet (optionnellement tronqué)."""
    p = _path(name)
    if not p.exists():
        return {"status": "ok", "channel": name, "content": "", "lines": 0}
    limit = max_lines or MAX_READ_LINES
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > limit:
        lines = lines[-limit:]
    return {"status": "ok", "channel": name, "content": "\n".join(lines), "lines": len(lines)}


def tail(name: str, lines: int = 100) -> Dict[str, Any]:
    """Retourne les dernières N lignes (efficace pour gros fichiers)."""
    p = _path(name)
    if not p.exists():
        return {"status": "ok", "channel": name, "content": "", "lines": 0}
    n = max(1, min(lines, 10_000))
    # lecture depuis la fin : on lit les derniers blocs
    try:
        with p.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = min(size, 64 * 1024 * (n // 2000 + 1))
            f.seek(max(0, size - block))
            data = f.read().decode("utf-8", errors="replace")
        all_lines = data.splitlines()
        last = all_lines[-n:]
        return {"status": "ok", "channel": name, "content": "\n".join(last), "lines": len(last)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def clear(name: str) -> Dict[str, Any]:
    p = _path(name)
    if p.exists():
        p.unlink()
    return {"status": "ok", "channel": name, "cleared": True}


def info(name: str) -> Dict[str, Any]:
    p = _path(name)
    if not p.exists():
        return {"status": "ok", "channel": name, "exists": False}
    lines = sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
    return {"status": "ok", "channel": name, "exists": True,
            "size": p.stat().st_size, "lines": lines, "path": str(p)}


def _maybe_rotate(p: Path) -> None:
    """Rotation simple : au-delà de MAX_FILE_MB, on garde <name>.1.log."""
    try:
        if p.exists() and p.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            bak = p.with_suffix(".1.log")
            if bak.exists():
                bak.unlink()
            p.rename(bak)
    except Exception:
        pass
