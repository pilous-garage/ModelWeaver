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
                  adresse_runtime_id: Optional[int] = None,
                  theo_override: Optional[Dict[str, float]] = None) -> Optional[int]:
    """Ouvre le suivi budgétaire d'une sub_task (avec budget théorique).

    Idempotent : si un suivi open existe déjà pour cette sub_task, on ne le
    duplique pas (retourne son id). Le budget théorique est calculé dès
    maintenant (le calculateur l'estime pour le modèle choisi), OU fourni par
    `theo_override` (ex. la part du pipeline posée par la découpe).
    """
    try:
        cur = ws.conn.execute(
            "SELECT tracking_id FROM task_budget_tracking "
            "WHERE sub_task_id = ? AND status = 'open'",
            (sub_task_id,)).fetchone()
        if cur:
            return cur["tracking_id"]
        theo = theo_override if theo_override is not None else (
            _theoretical_budget(cat, sub_task_type, difficulty, model_id or 0)
            if cat and model_id else {})
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


def _workspace_for(sub_task_id: int, task_id: Optional[int]):
    """Résout (WorkspaceDB, workspace_id) pour une sub_task/task donnée.

    La table task_budget_tracking vit dans la DB workspace (une par projet) —
    il faut retrouver LE bon fichier .db et le workspace_id de la sub_task.
    """
    try:
        from modules.sql.workspace import WorkspaceDB, _default_workspace_db
        db = WorkspaceDB(db_path=_default_workspace_db())
        if sub_task_id:
            row = db.conn.execute(
                "SELECT workspace_id FROM sub_tasks WHERE sub_task_id = ?",
                (sub_task_id,)).fetchone()
            if row:
                return db, row["workspace_id"]
        if task_id:
            row = db.conn.execute(
                "SELECT workspace_id FROM tasks WHERE task_id = ?",
                (task_id,)).fetchone()
            if row:
                return db, row["workspace_id"]
        db.close()
        return None, None
    except Exception:
        return None, None


def add_usage(sub_task_id: int, task_id: Optional[int] = None,
              tok_in: int = 0, tok_out: int = 0, tok_think: int = 0,
              temps: float = 0.0, req: int = 1) -> bool:
    """Incrémente le budget UTILISÉ d'une sub_task (en temps réel, à l'appel).

    Appelé par consume_call quand un LLM call est rattaché à une sub_task.
    Résout le bon WorkspaceDB depuis la sub_task. Best-effort.
    """
    try:
        db, ws_id = _workspace_for(sub_task_id, task_id)
        if db is None:
            return False
        try:
            ws = db.for_workspace(ws_id) if ws_id else db
            conn = ws.conn if hasattr(ws, "conn") else db.conn
            # Résout type + difficulté de la sub_task (pour le théorique futur).
            st = conn.execute(
                "SELECT sub_task_type, difficulty FROM sub_tasks WHERE sub_task_id = ?",
                (sub_task_id,)).fetchone() if conn else None
            st_type = st["sub_task_type"] if st else ""
            st_diff = st["difficulty"] if st else "medium"
            cur = conn.execute(
                "SELECT tracking_id, model_id, assigned_to FROM task_budget_tracking "
                "WHERE sub_task_id = ?", (sub_task_id,)).fetchone()
            if cur is None:
                # Suivi pas encore ouvert : on l'ouvre léger (sans théorique) — le
                # théorique sera posé à l'ouverture réelle (attribution).
                conn.execute("""
                    INSERT INTO task_budget_tracking
                        (workspace_id, task_id, sub_task_id, task_type, difficulty,
                         used_tok_in, used_tok_out, used_tok_think, used_req,
                         used_temps, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
                    ON CONFLICT(sub_task_id) DO UPDATE SET
                        used_tok_in = task_budget_tracking.used_tok_in + excluded.used_tok_in,
                        used_tok_out = task_budget_tracking.used_tok_out + excluded.used_tok_out,
                        used_tok_think = task_budget_tracking.used_tok_think + excluded.used_tok_think,
                        used_req = task_budget_tracking.used_req + excluded.used_req,
                        used_temps = task_budget_tracking.used_temps + excluded.used_temps,
                        updated_at = strftime('%s','now')
                """, (ws_id or '', task_id, sub_task_id, st_type, st_diff,
                      tok_in, tok_out, tok_think, req, temps))
            else:
                conn.execute("""
                    UPDATE task_budget_tracking SET
                        used_tok_in = used_tok_in + ?,
                        used_tok_out = used_tok_out + ?,
                        used_tok_think = used_tok_think + ?,
                        used_req = used_req + ?,
                        used_temps = used_temps + ?,
                        updated_at = strftime('%s','now')
                    WHERE sub_task_id = ?
                """, (tok_in, tok_out, tok_think, req, temps, sub_task_id))
            conn.commit()
            return True
        finally:
            db.close()
    except Exception:
        return False


def close_tracking(ws, cat, sub_task_id: int) -> bool:
    """Ferme le suivi d'une sub_task (le budget utilisé a déjà été cumulé
    en temps réel par add_usage — rien à reconstruire depuis le catalogue).
    Retourne True si le suivi a été fermé.
    """
    try:
        tr = ws.conn.execute(
            "SELECT tracking_id FROM task_budget_tracking "
            "WHERE sub_task_id = ? AND status = 'open'",
            (sub_task_id,)).fetchone()
        if not tr:
            return False
        ws.conn.execute("""
            UPDATE task_budget_tracking SET
                status = 'closed', closed_at = ?, updated_at = ?
            WHERE tracking_id = ?
        """, (_now(), _now(), tr["tracking_id"]))
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