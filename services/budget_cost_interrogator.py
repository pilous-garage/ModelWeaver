"""budget_cost_interrogator — interrogation systémique des coûts/quotas/keys.

Service à tick : périodiquement, interroge l'état des coûts/quotas/keys
depuis budget_cost (sources), info_llm (adresses), runtime_llm (usage réel)
et génère un rapport d'écart (budget effectif vs consommation).

Branché sur ServiceTicker (mode permanent ou éphémère selon interval).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from modules.sqlite.budget_cost import db as bc_db, read as bc_read
from modules.sqlite.runtime_llm import db as rl_db, read as rl_read
from modules.sqlite.info_llm import db as il_db, read as il_read


def interrogate() -> Dict[str, Any]:
    """Interrogation systémique : pour chaque provider/endpoint/adress,
    compare budget déclaré (budget_cost) vs consommation réelle (runtime_llm)."""
    bc = bc_db()
    rl = rl_db()
    il = il_db()
    report: Dict[str, Any] = {"providers": [], "timestamp": time.time()}
    try:
        # Adresses resolues (info_llm)
        adresses = il_read.list_adresses(il) if hasattr(il_read, "list_adresses") \
            else []
        for ad in adresses:
            provider = ad.get("provider_ref", "?")
            # Budget déclaré (human > general > guess)
            human = bc_read.list_human(bc, "human_budget")
            # Consommation réelle (runtime_llm.budget_consumption)
            cons = rl_read.list_budget_consumption(rl) if hasattr(rl_read, "list_budget_consumption") \
                else []
            report["providers"].append({
                "provider": provider,
                "adress_id": ad.get("adresse_id"),
                "budget_declared": len(human),
                "consumption_entries": len(cons),
            })
    finally:
        bc.close(); rl.close(); il.close()
    return report


def register_tick(st: Any, interval_s: float = 60.0) -> None:
    """Enregistre l'interrogateur sur le ServiceTicker."""
    st.register("budget_cost_interrogator", interval_s=interval_s,
                fn=interrogate, cmd="services.budget_cost_interrogator:interrogate")
