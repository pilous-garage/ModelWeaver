"""Logs du watcher — deux niveaux avec rotation (max 10 Mo).

- **Détaillé** : `~/.modelweaver/logs/watcher_detail.log` — chaque cycle, les
  problèmes détectés, les actions appliquées, les états. Pour vérifier que ça
  tourne correctement et débugger en profondeur.
- **Synthèse** : `~/.modelweaver/logs/watcher_summary.log` — une ligne par
  problème récurrent : {ts, type, résolu(script/llm/non_résolu), details}.
  Pour analyser les tendances et les points à traiter.

Les deux fichiers tournent (max 10 Mo, 3 backups) — réutilise la rotation de
llm_conversation_log pour rester cohérent et éviter de dupliquer.
"""

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from services._common import mw_home

MAX_BYTES = 10 * 1024 * 1024  # 10 Mo


def _rotate(path: Path) -> None:
    """Rotation style logrotate : .1 → .2 → .3, puis reset du fichier principal."""
    if not path.exists():
        return
    if path.stat().st_size < MAX_BYTES:
        return
    for i in range(3, 0, -1):
        src = path.with_suffix(f"{path.suffix}.{i-1}") if i > 1 else path
        dst = path.with_suffix(f"{path.suffix}.{i}")
        if src.exists():
            if dst.exists():
                dst.unlink()
            src.rename(dst)
    path.touch()


def _append(name: str, line: str) -> None:
    try:
        log_dir = Path(mw_home()) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / name
        _rotate(path)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass  # le log ne doit jamais casser le watcher


def detail(msg: str) -> None:
    """Ligne du log détaillé (tout ce qui se passe)."""
    _append("watcher_detail.log", f"[{time.strftime('%H:%M:%S')}] {msg}")


def summary(problem_type: str, resolution: str, details: str = "",
            count: int = 1) -> None:
    """Ligne de la synthèse des problèmes récurrents.

    resolution : 'script' | 'llm' | 'non_resolu' | 'warning'
    """
    safe = details.replace("|", "/").replace("\n", " ")[:200]
    _append("watcher_summary.log",
            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {problem_type} | "
            f"{resolution} | x{count} | {safe}")


def cycle_detail(cycle_n: int, st: Dict[str, Any], problems: List[Dict],
                 actions: List[Dict], warnings: List[Dict]) -> None:
    """Log détaillé d'un cycle complet (état + problèmes + actions + warnings)."""
    detail(f"── cycle #{cycle_n} ──")
    detail(f"état: agents={len(st.get('agents', []))} "
           f"runtime={len(st.get('runtime', []))} "
           f"tasks={len(st.get('tasks', []))} "
           f"issues={len(st.get('issues', []))} "
           f"wait_for={len(st.get('wait_for', []))}")
    for p in problems:
        detail(f"  problème: {p.get('type')} agent={p.get('agent_id')} "
               f"détail={p.get('details', '')[:120]}")
    for a in actions:
        detail(f"  action: {a.get('type')} → {a.get('action', '')[:120]}")
    for w in warnings:
        detail(f"  ⚠ warning: {w.get('type')} → {w.get('detail', '')[:120]}")
