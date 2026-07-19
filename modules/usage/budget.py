"""Consultation du budget reellement consomme (USD) et des modeles free-tier.

Le budget USD est persiste par le rassembleur (usage_collector) dans
really_used_budget + budget_consumption. Ce module expose des accesseurs
lisibles pour la GUI / l'API.
"""

from typing import Dict, Any, List, Optional

from modules.sql.db import ModelWeaverDB, CatalogueDB


def get_budget_summary(mw: Optional[ModelWeaverDB] = None) -> Dict[str, Any]:
    """Retourne le resume du budget USD consomme par cible.

    Structure:
    {
      "total_usd": float,
      "by_provider": {provider_ref: usd},
      "by_model": {model_ref: usd},
      "by_agent": {agent_id: usd},
    }
    """
    own = mw is None
    if own:
        mw = ModelWeaverDB()
    try:
        total = mw.conn.execute(
            "SELECT COALESCE(SUM(used),0) FROM budget_consumption bc "
            "JOIN really_used_budget rb ON rb.id=bc.budget_id "
            "WHERE rb.budget_tag_code='usd_total'").fetchone()[0]

        def _by(ttype: str) -> Dict[str, float]:
            rows = mw.conn.execute(
                "SELECT rb.target_ref, COALESCE(SUM(bc.used),0) AS used "
                "FROM really_used_budget rb "
                "JOIN budget_consumption bc ON bc.budget_id=rb.id "
                "WHERE rb.budget_tag_code='usd_total' AND rb.target_type=? "
                "GROUP BY rb.target_ref ORDER BY used DESC", (ttype,)).fetchall()
            return {r["target_ref"]: float(r["used"]) for r in rows}

        return {
            "total_usd": float(total or 0.0),
            "by_provider": _by("provider"),
            "by_model": _by("model"),
            "by_agent": _by("agent"),
        }
    finally:
        if own:
            mw.close()


def get_free_tier_models(cat: Optional[CatalogueDB] = None) -> List[Dict[str, Any]]:
    """Liste les modeles marques free-tier (cout nul) pour tous les providers.

    Retourne [{provider_ref, model_ref, provider_model_name, context_window_tokens}].
    """
    own = cat is None
    if own:
        cat = CatalogueDB()
    try:
        rows = cat.conn.execute(
            "SELECT p.ref AS provider_ref, m.ref AS model_ref, "
            "pm.provider_model_name, pm.context_window_tokens "
            "FROM provider_models pm "
            "JOIN catalogue_providers p ON p.id=pm.provider_id "
            "JOIN catalogue_models m ON m.id=pm.model_id "
            "WHERE pm.free_tier=1").fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            cat.close()


def get_budget_rows(mw: Optional[ModelWeaverDB] = None,
                    limit: int = 100) -> List[Dict[str, Any]]:
    """Lignes detaillees really_used_budget + budget_consumption."""
    own = mw is None
    if own:
        mw = ModelWeaverDB()
    try:
        rows = mw.conn.execute(
            "SELECT rb.target_type, rb.target_ref, rb.window, "
            "bc.used, bc.updated_at "
            "FROM really_used_budget rb "
            "JOIN budget_consumption bc ON bc.budget_id=rb.id "
            "WHERE rb.budget_tag_code='usd_total' "
            "ORDER BY bc.used DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            mw.close()
