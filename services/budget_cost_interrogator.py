"""budget_cost_interrogator — interrogation systémique des coûts/quotas/keys.

Service à tick : périodiquement, interroge l'état des coûts/quotas/keys
depuis budget_cost (sources), info_llm (adresses), runtime_llm (usage réel)
et GÉNÈRE runtime_llm.budget_final depuis compute_budget() (hiérarchie
humain > general > guess).

Branché sur ServiceTicker.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from modules.sqlite.budget_cost import db as bc_db, read as bc_read
from modules.sqlite.runtime_llm import db as rl_db, read as rl_read, write as rl_write
from modules.sqlite.info_llm import db as il_db, read as il_read


# Types de budget suivis (budget_tags)
_BUDGET_TYPES = ["req_per_min", "req_per_day", "tok_per_min", "tok_per_day",
                 "cost_per_day", "cost_per_month"]


def interrogate() -> Dict[str, Any]:
    """Interrogation + génération budget_final : pour chaque provider/adress,
    calcule le budget effectif (compute_budget) et l'écrit dans runtime_llm."""
    bc = bc_db()
    rl = rl_db()
    il = il_db()
    report: Dict[str, Any] = {"providers": [], "generated": 0, "timestamp": time.time()}
    try:
        adresses = il_read.list_adresses(il) if hasattr(il_read, "list_adresses") \
            else []
        providers = {ad.get("provider_ref", "?") for ad in adresses}
        providers.add("openai")  # au moins un provider de référence
        for provider in providers:
            for btype in _BUDGET_TYPES:
                eff = bc_read.compute_budget(bc, btype, provider=provider)
                if eff["source"] == "none":
                    continue  # pas de limite déclarée → pas de budget_final
                budget_ref = f"{provider}:{btype}"
                limit_tokens = int(eff["budget_reel"] or 0) \
                    if "tok" in btype or "req" in btype else 0
                limit_cost = float(eff["budget_reel"] or 0) \
                    if "cost" in btype else 0
                rl_write.upsert_budget_final(
                    budget_ref, limit_cost=limit_cost,
                    limit_tokens=limit_tokens, reset_at=int(time.time()) + 86400,
                    db=rl)
                report["generated"] += 1
            report["providers"].append({"provider": provider})
    finally:
        bc.close(); rl.close(); il.close()
    return report


def register_tick(st: Any, interval_s: float = 60.0) -> None:
    """Enregistre l'interrogateur sur le ServiceTicker."""
    st.register("budget_cost_interrogator", interval_s=interval_s,
                fn=interrogate, cmd="services.budget_cost_interrogator:interrogate")
