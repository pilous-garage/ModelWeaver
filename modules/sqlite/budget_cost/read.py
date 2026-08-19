"""budget_cost.read — lectures budget_cost.db (human/general/guess)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db


def get_human(d: Db, budget_id: int) -> Optional[Dict[str, Any]]:
    r = d._conn.execute(
        "SELECT * FROM human_budget WHERE id = ?", (budget_id,)).fetchone()
    return dict(r) if r else None


def list_human(d: Db, type_: str = "", scope: str = "-1") -> List[Dict[str, Any]]:
    q = "SELECT * FROM human_budget WHERE 1=1"
    p: list = []
    if type_:
        q += " AND type = ?"
        p.append(type_)
    if scope != "-1":
        q += " AND (provider=? OR modele=? OR project=? OR team=? OR agent=?)"
        p += [scope] * 5
    q += " ORDER BY type, id"
    return [dict(r) for r in d._conn.execute(q, p).fetchall()]


def list_general(d: Db, type_: str = "") -> List[Dict[str, Any]]:
    q = "SELECT * FROM general_budget"
    p: list = []
    if type_:
        q += " WHERE type = ?"
        p.append(type_)
    q += " ORDER BY type, id"
    return [dict(r) for r in d._conn.execute(q, p).fetchall()]


def list_guess_bundles(d: Db, target_kind: str = "",
                       target_ref: str = "") -> List[Dict[str, Any]]:
    q = "SELECT * FROM guess_bundle_quota WHERE 1=1"
    p: list = []
    if target_kind:
        q += " AND target_kind = ?"
        p.append(target_kind)
    if target_ref:
        q += " AND target_ref = ?"
        p.append(target_ref)
    q += " ORDER BY bundle_id"
    return [dict(r) for r in d._conn.execute(q, p).fetchall()]


def list_guesses(d: Db, bundle_id: int) -> List[Dict[str, Any]]:
    return [dict(r) for r in d._conn.execute(
        "SELECT * FROM guess_quota WHERE bundle_id = ? ORDER BY guess_id",
        (bundle_id,)).fetchall()]


def list_guess_bundles_cost(d: Db, target_kind: str = "",
                            target_ref: str = "") -> List[Dict[str, Any]]:
    q = "SELECT * FROM guess_bundle_cost WHERE 1=1"
    p: list = []
    if target_kind:
        q += " AND target_kind = ?"
        p.append(target_kind)
    if target_ref:
        q += " AND target_ref = ?"
        p.append(target_ref)
    q += " ORDER BY bundle_id"
    return [dict(r) for r in d._conn.execute(q, p).fetchall()]


def list_guesses_cost(d: Db, bundle_id: int) -> List[Dict[str, Any]]:
    return [dict(r) for r in d._conn.execute(
        "SELECT * FROM guess_cost WHERE bundle_id = ? ORDER BY guess_id",
        (bundle_id,)).fetchall()]


def _match_scope(row: Dict[str, Any], provider: str, modele: str,
                 project: str, team: str, agent: str) -> bool:
    """Une ligne matche si son scope = -1 (tous) ou = la valeur cherchée."""
    for col, val in (("provider", provider), ("modele", modele),
                     ("project", project), ("team", team), ("agent", agent)):
        s = row.get(col, "-1")
        if s != "-1" and s != val:
            return False
    return True


def compute_budget(d: Db, type_: str, provider: str = "-1", modele: str = "-1",
                   project: str = "-1", team: str = "-1", agent: str = "-1",
                   guess_fallback: bool = True) -> Dict[str, Any]:
    """Calcule le budget effectif selon la hiérarchie humain > general > guess.

    Retourne budget_reel (nb), budget_hard (% refus), budget_soft (% toléré).
    - humain : priorité max (s'il existe une déclaration scope-matched)
    - general : limites de sécurité globales
    - guess : uniquement pour les QUOTAS (guess_fallback=True)
    """
    # 1. HUMAN (priorité haute)
    for r in list_human(d, type_):
        if _match_scope(r, provider, modele, project, team, agent):
            return {"type": type_, "source": "human", "budget_reel": r["nb"],
                    "budget_hard": r["hard_limit"], "budget_soft": r["soft_limit"]}
    # 2. GENERAL (sécurité globale)
    for r in list_general(d, type_):
        if _match_scope(r, provider, modele, project, team, agent):
            return {"type": type_, "source": "general", "budget_reel": r["nb"],
                    "budget_hard": r["hard_limit"], "budget_soft": r["soft_limit"]}
    # 3. GUESS (quotas seulement)
    if guess_fallback:
        bundles = list_guess_bundles(d, target_kind="provider", target_ref=provider)
        for b in bundles:
            for g in list_guesses(d, b["bundle_id"]):
                if g["type_limite"] == type_:
                    # guess = estimation ; on prend large_estimate comme reel
                    return {"type": type_, "source": "guess",
                            "budget_reel": g["large_estimate"],
                            "budget_hard": 100.0, "budget_soft": 120.0}
    # Aucun budget déclaré → pas de limite (pas infini, absent)
    return {"type": type_, "source": "none", "budget_reel": None,
            "budget_hard": None, "budget_soft": None}
