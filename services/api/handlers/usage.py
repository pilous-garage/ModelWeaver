from services.api._shared import _quiet
from services.api.router import register

# ── Usage ───────────────────────────────────────────────────────────────

def op_usage_budget(params):
    from services.usage import get_budget
    return get_budget(params)


def op_usage_free_tier(params):
    from services.usage import get_free_tier
    return get_free_tier(params)


# ── Tarif ───────────────────────────────────────────────────────────────

def _op_tarif_info(_params):
    from services.tarif import tarif_info
    return tarif_info()


def _op_tarif_sync(url=None):
    from services.tarif import sync_tarif
    return sync_tarif(url)


# ── Route registration ─────────────────────────────────────────────────

register("usage/budget",    op_usage_budget)
register("usage/free_tier", op_usage_free_tier)
register("tarif/info",      lambda p: _quiet(_op_tarif_info, p))
register("tarif/sync",      lambda p: _quiet(_op_tarif_sync, p.get("url")))
