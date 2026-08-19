"""score_experience_manager — gestionnaire d'expérience pour les scores.

Tick (ex 60s) : lit les tâches root complétées (task domain), remonte le
chemin emprunté (modèles/adresses utilisés), et applique des bonus/malus
dans score.score_adjust → consolide score_adress / score_model (init 1.0).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from modules.sqlite.task import db as task_db, read as task_read
from modules.sqlite.score import write as sc_write


def _completed_root_tasks(d) -> List[Dict[str, Any]]:
    """Tâches root (task parent NULL) en status done/supervised/completed."""
    return [dict(r) for r in d._conn.execute(
        "SELECT * FROM tasks WHERE parent_task_id IS NULL "
        "AND status IN ('done','supervised','completed') "
        "ORDER BY task_id DESC LIMIT 50").fetchall()]


def _model_path_for_task(task: Dict[str, Any]) -> List[str]:
    """Remonte les appels LLM de la tâche via model_call_log (runtime_llm)."""
    # Import ici pour éviter cycle au import-top
    from modules.sqlite.runtime_llm import db as rl_db
    rl = rl_db()
    try:
        rows = rl._conn.execute(
            "SELECT DISTINCT model_ref, adresse_id FROM model_call_log "
            "WHERE task_id = ?", (task.get("task_id"),)).fetchall()
        return [dict(r) for r in rows]
    finally:
        rl.close()


def analyze() -> Dict[str, Any]:
    """Une passe : pour chaque tâche root complétée, bonus aux modèles/adresses."""
    td = task_db()
    try:
        tasks = _completed_root_tasks(td)
        n = 0
        for t in tasks:
            path = _model_path_for_task(t)
            for p in path:
                model_ref = p.get("model_ref")
                adress_id = p.get("adresse_id")
                if model_ref:
                    sc_write.adjust_score_experience("model", model_ref, bonus=0.01,
                                          reason=f"root_task {t.get('task_id')}")
                if adress_id:
                    sc_write.adjust_score_experience("adress", str(adress_id), bonus=0.01,
                                          reason=f"root_task {t.get('task_id')}")
            n += 1
        return {"ok": True, "tasks_processed": n}
    finally:
        td.close()


def register_tick(st, interval_s: float = 60.0) -> None:
    st.register("score_experience_manager", interval_s=interval_s, fn=analyze,
                cmd="services.score_experience_manager:analyze")
