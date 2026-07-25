"""Service usage : consultation du budget et free-tier.

Usage depuis les handlers daemon :
    from services.usage import get_budget, get_free_tier
"""
from modules.usage.usage_module import get_budget_summary, get_budget_rows, get_free_tier_models


def get_budget(params: dict) -> dict:
    from services.api._shared import _get_mw
    mw = _get_mw()
    return {
        "summary": get_budget_summary(mw),
        "rows": get_budget_rows(mw),
    }


def get_free_tier(_params: dict) -> dict:
    from services.api._shared import _get_cat
    cat = _get_cat()
    return {"free_tier": get_free_tier_models(cat)}
