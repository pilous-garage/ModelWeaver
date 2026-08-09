"""check_error_type — classifie une erreur LLM pour que le FSM branche.

Le FSM passe le message brut (ex. résolution de `_last_call_error`, qui est un
BridgeError str(è : `[RATE_LIMIT] provider/model: message`). On classifie par
parsing du texte (catégorie entre crochets + patterns limit_type + retry_after),
avec repli sur LLMManager.classify_error si le format diffère. Ne fait AUCUN
appel LLM, ne modifie rien.
"""

import re

_CATEGORIES = ("AUTH", "RATE_LIMIT", "CONTEXT", "SERVER", "TIMEOUT", "UNKNOWN")


def _parse_text(raw: str) -> dict:
    """Parse un message de type BridgeError : [CATEGORIE] ... [limit_type] [retry=s]."""
    m = re.search(r"\[(AUTH|RATE_LIMIT|CONTEXT|SERVER|TIMEOUT|UNKNOWN)\]", raw)
    category = m.group(1).lower() if m else ""
    limit_type = ""
    if re.search(r"per[-_ ]?day|daily|perday", raw, re.I):
        limit_type = "daily_quota"
    elif re.search(r"credit|balance|insufficient|402|solde", raw, re.I):
        limit_type = "quota"
    elif re.search(r"context|max_input|tokens.*limit|too many tokens", raw, re.I):
        limit_type = "tokens"
    elif re.search(r"rate|rpm|requests per", raw, re.I):
        limit_type = "rpm"
    retry_after = 0.0
    m2 = re.search(
        r"retry[-_ =]?after[^\d]{0,10}(\d+)|retry in (\d+)|again in (\d+)|retry[=_](?:seconds?)?=(\d+)",
        raw, re.I)
    if m2:
        retry_after = float(next(g for g in m2.groups() if g is not None) or 0)
    if not category:
        # Repli : détection par contenu quand il n'y a pas de préfixe [..]
        body = raw.lower()
        if re.search(r"401|403|invalid api|unauthorized|api key", body):
            category = "auth"
        elif re.search(r"429|quota exceeded|rate limit|too many requests", body):
            category = "rate_limit"
            if not limit_type:
                limit_type = "rpm"
        elif re.search(r"408|504|timed out|timeout", body):
            category = "timeout"
        elif re.search(r"5\d\d|internal server|bad gateway", body):
            category = "server"
    return {"category": category or "unknown", "limit_type": limit_type,
            "retry_after": retry_after}


def _recommend_action(category: str, limit_type: str, retry_after: float) -> str:
    if category == "auth":
        if limit_type == "quota":
            return "blacklist_provider"  # compte sans crédit : inutile de retenter
        return "switch_model"
    if category == "rate_limit":
        if limit_type == "daily_quota":
            return "retry_long"         # quota 24h : reposer le modèle longtemps
        if retry_after and retry_after <= 120:
            return "retry_short"
        return "retry_long"
    if category == "context":
        return "context_reduce"
    if category in ("server", "timeout"):
        return "retry_short"
    return "retry_short"


def exec(inputs: dict, home: str) -> dict:
    raw = str(inputs.get("error", "") or "").strip()
    provider_ref = inputs.get("provider_ref", "") or ""
    model_ref = inputs.get("model_ref", "") or ""
    if not raw:
        return {"ok": False, "category": "unknown", "limit_type": "",
                "action": "retry_short",
                "message": "aucune erreur à classifier"}

    info = _parse_text(raw)
    category = info["category"]
    limit_type = info["limit_type"]
    retry_after = info["retry_after"]

    # Repli : si le parse textuel n'a rien trouvé, tenter classify_error.
    if category == "unknown" or not limit_type:
        try:
            from modules.llm_manager.llm_manager import LLMManager
            from modules.sql.catalogue_repo import CatalogueDB
            llm_mgr = LLMManager(CatalogueDB())
            err = llm_mgr.classify_error(Exception(raw), provider_ref, model_ref)
            if category == "unknown":
                category = getattr(getattr(err, "category", None), "value", "unknown")
            if not limit_type:
                limit_type = getattr(err, "limit_type", "") or ""
            if not retry_after:
                retry_after = float(getattr(err, "retry_after_seconds", 0) or 0)
        except Exception:
            pass

    action = _recommend_action(category, limit_type, retry_after)
    return {"ok": True, "category": category, "limit_type": limit_type,
            "action": action, "retry_after": retry_after,
            "message": raw[:400]}


__skills__ = ["exec"]
