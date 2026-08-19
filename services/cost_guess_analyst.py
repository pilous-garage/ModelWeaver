"""cost_guess_analyst — analyse les séquences erreur/succès des guess de COUT.

Tick léger (ex 5 min) : lit runtime_llm.cost_error_seq, calcule le taux
d'erreur par guess, ajuste budget_cost.guess_cost (precision/coherence) +
budget_cost.guess_bundle_cost (scores globaux).
"""

from __future__ import annotations

import time
from typing import Any, Dict

from modules.sqlite.runtime_llm import db as rl_db
from modules.sqlite.budget_cost import db as bc_db, read as bc_read, write as bc_write


def _read_seq(d) -> list:
    return [dict(r) for r in d._conn.execute(
        "SELECT * FROM cost_error_seq ORDER BY seq_id").fetchall()]


def analyze() -> Dict[str, Any]:
    rl = rl_db()
    bc = bc_db()
    try:
        seqs = _read_seq(rl)
        agg: Dict[int, Dict[str, int]] = {}
        for s in seqs:
            gid = s["guess_id"]
            a = agg.setdefault(gid, {"total": 0, "errors": 0})
            a["total"] += 1
            if s["outcome"] == "error":
                a["errors"] += 1
        for gid, a in agg.items():
            err_rate = a["errors"] / a["total"] if a["total"] else 0
            new_precision = max(0.0, min(1.0, 1.0 - err_rate))
            bc_write.update_guess_cost_precision(bc, gid, new_precision)
        bundles = bc_read.list_guess_bundles_cost(bc)
        for b in bundles:
            gid = b["bundle_id"]
            guesses = bc_read.list_guesses_cost(bc, gid)
            if guesses:
                avg_prec = sum(g.get("precision", 0) for g in guesses) / len(guesses)
                avg_coh = sum(g.get("coherence", 0) for g in guesses) / len(guesses)
                bc_write.update_guess_bundle_cost_scores(bc, gid, avg_prec, avg_coh)
        return {"ok": True, "guesses_analyzed": len(agg), "seqs": len(seqs)}
    finally:
        rl.close(); bc.close()


def register_tick(st, interval_s: float = 300.0) -> None:
    st.register("cost_guess_analyst", interval_s=interval_s, fn=analyze,
                cmd="services.cost_guess_analyst:analyze")
