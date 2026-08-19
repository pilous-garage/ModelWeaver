"""budget_cost.write — écritures budget_cost.db (human/general/guess)."""

from __future__ import annotations

from typing import Any, Dict

from modules.sqlite.base import Db


def add_human(d: Db, type_: str, nb: float, provider: str = "-1",
              modele: str = "-1", project: str = "-1", team: str = "-1",
              agent: str = "-1", hard_limit: float = 100.0,
              soft_limit: float = 100.0) -> Dict[str, Any]:
    cur = d._conn.execute(
        "INSERT INTO human_budget (type, nb, provider, modele, project, team, "
        "agent, hard_limit, soft_limit) VALUES (?,?,?,?,?,?,?,?,?)",
        (type_, nb, provider, modele, project, team, agent,
         hard_limit, soft_limit))
    d._conn.commit()
    return {"ok": True, "id": cur.lastrowid, "type": type_}


def add_general(d: Db, type_: str, nb: float, provider: str = "-1",
                modele: str = "-1", project: str = "-1", team: str = "-1",
                agent: str = "-1", hard_limit: float = 100.0,
                soft_limit: float = 100.0) -> Dict[str, Any]:
    cur = d._conn.execute(
        "INSERT INTO general_budget (type, nb, provider, modele, project, team, "
        "agent, hard_limit, soft_limit) VALUES (?,?,?,?,?,?,?,?,?)",
        (type_, nb, provider, modele, project, team, agent,
         hard_limit, soft_limit))
    d._conn.commit()
    return {"ok": True, "id": cur.lastrowid, "type": type_}


def add_guess_bundle(d: Db, target_kind: str, target_ref: str,
                     score_precision_global: float = 0,
                     score_coherence_global: float = 0) -> Dict[str, Any]:
    cur = d._conn.execute(
        "INSERT INTO guess_bundle_quota (target_kind, target_ref, "
        "score_precision_global, score_coherence_global) VALUES (?,?,?,?)",
        (target_kind, target_ref, score_precision_global,
         score_coherence_global))
    d._conn.commit()
    return {"ok": True, "bundle_id": cur.lastrowid}


def add_guess_quota(d: Db, bundle_id: int, target_kind: str, target_ref: str,
                    type_limite: str, unit: str, low_estimate: float = 0,
                    large_estimate: float = 0, window_reset_low: int = 0,
                    window_reset_high: int = 0, coherence: float = 0,
                    precision: float = 0) -> Dict[str, Any]:
    cur = d._conn.execute(
        "INSERT INTO guess_quota (bundle_id, target_kind, target_ref, "
        "type_limite, unit, low_estimate, large_estimate, window_reset_low, "
        "window_reset_high, coherence, precision) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (bundle_id, target_kind, target_ref, type_limite, unit, low_estimate,
         large_estimate, window_reset_low, window_reset_high, coherence,
         precision))
    d._conn.commit()
    return {"ok": True, "guess_id": cur.lastrowid}


def add_guess_bundle_cost(d: Db, target_kind: str, target_ref: str,
                          score_precision_global: float = 0,
                          score_coherence_global: float = 0) -> Dict[str, Any]:
    cur = d._conn.execute(
        "INSERT INTO guess_bundle_cost (target_kind, target_ref, "
        "score_precision_global, score_coherence_global) VALUES (?,?,?,?)",
        (target_kind, target_ref, score_precision_global,
         score_coherence_global))
    d._conn.commit()
    return {"ok": True, "bundle_id": cur.lastrowid}


def add_guess_cost(d: Db, bundle_id: int, target_kind: str, target_ref: str,
                   type_limite: str, unit_in: str, unit_out: str,
                   low_estimate: float = 0, large_estimate: float = 0,
                   window_reset_low: int = 0, window_reset_high: int = 0,
                   depends_on_time: int = 0, coherence: float = 0,
                   precision: float = 0) -> Dict[str, Any]:
    cur = d._conn.execute(
        "INSERT INTO guess_cost (bundle_id, target_kind, target_ref, "
        "type_limite, unit_in, unit_out, low_estimate, large_estimate, "
        "window_reset_low, window_reset_high, depends_on_time, coherence, "
        "precision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (bundle_id, target_kind, target_ref, type_limite, unit_in, unit_out,
         low_estimate, large_estimate, window_reset_low, window_reset_high,
         depends_on_time, coherence, precision))
    d._conn.commit()
    return {"ok": True, "guess_id": cur.lastrowid}
