"""llm_usage/task_track — SUIVI BUDGÉTAIRE PAR TÂCHE (Idée 18, section N).

Chaque tâche (ou sub_task) porte le budget qu'elle DEVRAIT théoriquement
consommer (estimé par le calculateur à partir du travail + effort du modèle)
ET le budget RÉELLEMENT utilisé par les appels LLM qui lui sont rattachés.

Boucle d'apprentissage : théorique vs utilisé → on affine task_level_cost /
llm_effort_ratio (le "travail" réel d'une tâche), et l'allocateur connaît le
budget d'une tâche avant de choisir le modèle (budget nécessaire).

Lien appels→tâche : meta_json de model_call_log porte task_id / sub_task_id /
task_type / difficulty (injecté par le FSM au moment de l'appel). Le "utilisé"
se reconstruit par requête sur le catalogue. Best-effort : ne lève jamais.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional


def _now() -> int:
    return int(time.time())


def _guess_niveau(difficulty: str) -> str:
    """difficulty métier (easy/medium/hard/expert) → niveau du scoring
    (debutant/junior/intermediaire/senior/expert)."""
    return {
        "easy": "debutant",
        "medium": "junior",
        "hard": "intermediaire",
        "expert": "senior",
    }.get((difficulty or "medium").lower(), "junior")


def _theoretical_budget(cat, sub_task_type: str, difficulty: str,
                        model_id: int):
    """Budget théorique d'une (sub_task, niveau) pour un modèle.

    = travail(task_type, niveau) × effort(modèle) — le produit du calculateur
    (llm_task_cost). Fallback heuristique si la synthèse n'existe pas encore.
    """
    try:
        niveau = _guess_niveau(difficulty)
        tt = cat.conn.execute(
            "SELECT id FROM scoring_task_types WHERE code = ?",
            (sub_task_type,)).fetchone()
        if tt:
            row = cat.conn.execute(
                "SELECT tok_in, tok_out, tok_think, req, temps, "
                "thinking_power, money, confiance FROM llm_task_cost "
                "WHERE model_id = ? AND task_type_id = ? AND niveau = ?",
                (model_id, tt["id"], niveau)).fetchone()
            if row:
                return {k: float(row[k] or 0) for k in (
                    "tok_in", "tok_out", "tok_think", "req", "temps",
                    "thinking_power", "money")}
    except Exception:
        pass
    return {"tok_in": 0, "tok_out": 0, "tok_think": 0, "req": 0,
            "temps": 0, "thinking_power": 0, "money": 0}


def open_tracking(ws, cat, workspace_id: str, sub_task_id: int,
                  task_id: Optional[int], sub_task_type: str,
                  difficulty: str, assigned_to: str = "",
                  model_id: Optional[int] = None,
                  adresse_runtime_id: Optional[int] = None) -> Optional[int]:
    """Ouvre le suivi budgétaire d'une sub_task (avec budget théorique).

    Idempotent : si un suivi open existe déjà pour cette sub_task, on ne le
    duplique pas (retourne son id). Le budget théorique est calculé dès
    maintenant (le calculateur l'estime pour le modèle choisi).
    """
    try:
        cur = ws.conn.execute(
            "SELECT tracking_id FROM task_budget_tracking "
            "WHERE sub_task_id = ? AND status = 'open'",
            (sub_task_id,)).fetchone()
        if cur:
            return cur["tracking_id"]
        theo = _theoretical_budget(cat, sub_task_type, difficulty, model_id or 0) \
            if cat and model_id else {}
        cur = ws.conn.execute("""
            INSERT INTO task_budget_tracking
                (workspace_id, task_id, sub_task_id, task_type, difficulty,
                 assigned_to, model_id, adresse_runtime_id,
                 theo_tok_in, theo_tok_out, theo_tok_think, theo_req,
                 theo_temps, theo_money, theo_thinking, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """, (workspace_id, task_id, sub_task_id, sub_task_type, difficulty,
              assigned_to, model_id, adresse_runtime_id,
              theo.get("tok_in", 0), theo.get("tok_out", 0),
              theo.get("tok_think", 0), theo.get("req", 0),
              theo.get("temps", 0), theo.get("money", 0),
              theo.get("thinking_power", 0)))
        ws.conn.commit()
        return cur.lastrowid if hasattr(cur, "lastrowid") else None
    except Exception:
        return None


def close_tracking(ws, cat, sub_task_id: int) -> bool:
    """Ferme le suivi d'une sub_task en reconstruisant le budget UTILISÉ.

    Le "utilisé" vient des appels LLM du catalogue dont le meta porte
    sub_task_id (meta_json.sub_task_id) — cumul des tokens/latence.
    Retourne True si le suivi a été fermé.
    """
    try:
        tr = ws.conn.execute(
            "SELECT tracking_id, model_id FROM task_budget_tracking "
            "WHERE sub_task_id = ? AND status = 'open'",
            (sub_task_id,)).fetchone()
        if not tr:
            return False
        # Cumul des appels du catalogue rattachés à cette sub_task.
        used = {"tok_in": 0, "tok_out": 0, "tok_think": 0, "req": 0, "temps": 0}
        if cat:
            rows = cat.conn.execute("""
                SELECT success, tokens_in, tokens_out, tokens_thinking, latency_ms
                FROM model_call_log
                WHERE meta_json LIKE ?
            """, (f'%"sub_task_id": {sub_task_id}%',)).fetchall()
            for r in rows:
                used["tok_in"] += r["tokens_in"] or 0
                used["tok_out"] += r["tokens_out"] or 0
                used["tok_think"] += r["tokens_thinking"] or 0
                used["req"] += 1
                used["temps"] += (r["latency_ms"] or 0) / 1000.0
        ws.conn.execute("""
            UPDATE task_budget_tracking SET
                used_tok_in = ?, used_tok_out = ?, used_tok_think = ?,
                used_req = ?, used_temps = ?, status = 'closed',
                closed_at = ?, updated_at = ?
            WHERE tracking_id = ?
        """, (used["tok_in"], used["tok_out"], used["tok_think"], used["req"],
              used["temps"], _now(), _now(), tr["tracking_id"]))
        ws.conn.commit()
        return True
    except Exception:
        return False


def list_open(ws, workspace_id: str = "") -> List[Dict[str, Any]]:
    """Les suivis ouverts (optionnellement filtrés par workspace)."""
    try:
        if workspace_id:
            rows = ws.conn.execute(
                "SELECT * FROM task_budget_tracking WHERE status = 'open' "
                "AND workspace_id = ? ORDER BY tracking_id",
                (workspace_id,)).fetchall()
        else:
            rows = ws.conn.execute(
                "SELECT * FROM task_budget_tracking WHERE status = 'open' "
                "ORDER BY tracking_id").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []