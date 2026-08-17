"""catalogue_manage — FONCTIONS add_* (Idée 18/P2).

Ajout d'entités au catalogue EN CASCADE sur les tables concernées. Le but :
qu'on ait TOUT via le catalogue, et qu'un ajout propage les tables dérivées.

Fonctions :
  - add_task_type(code, label)   → scoring_task_types + llm_task_type_score
                                     (init pour tous les modèles) + task_level_cost
                                     + task_level_stats (ratios par niveau).
  - add_domaine(code, label)     → scoring_domaines + llm_domaine_score (tous modèles).
  - add_provider(ref, ...)       → catalogue_providers (+ rien d'autre pour l'instant).
  - add_endpoint(provider, url)  → provider_endpoints.
  - add_model(ref, model_key)    → catalogue_models + scores par domaine/type
                                     + llm_task_type_score (init).
  - add_model_to_provider(...)   → provider_models + provider_model_address
                                     + adresse_runtime + cost/budget en cascade.
  - add_type_key(provider, tag)  → pose l'api_key_tag sur les adresses du provider
                                     + met à jour budget_final/cost (via seed).

Chaque fonction est idempotente (INSERT OR IGNORE) et best-effort (ne lève pas).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sql.catalogue_repo import CatalogueDB


def _cat(cat=None) -> CatalogueDB:
    return cat if cat is not None else CatalogueDB()


def add_task_type(code: str, label: str = "", cat=None) -> Dict[str, Any]:
    """Ajoute un type de tâche, en CASCADE : scoring_task_types + scores init
    (tous modèles) + task_level_cost + task_level_stats."""
    cat = _cat(cat)
    if not code:
        return {"ok": False, "error": "code requis"}
    try:
        cat.conn.execute(
            "INSERT OR IGNORE INTO scoring_task_types (code, label) VALUES (?, ?)",
            (code, label or code))
        row = cat.conn.execute(
            "SELECT id FROM scoring_task_types WHERE code = ?", (code,)).fetchone()
        tid = row["id"]
        # scores init (tous modèles avec model_key)
        cat.conn.execute("""
            INSERT OR IGNORE INTO llm_task_type_score (model_id, task_type_id)
            SELECT DISTINCT cm.id, ? FROM catalogue_models cm WHERE cm.model_key != ''
        """, (tid,))
        # task_level_cost (travail par niveau 1.0) + stats (ratios 1.0)
        for lvl in ("debutant", "junior", "intermediaire", "senior", "expert"):
            cat.conn.execute(
                "INSERT OR IGNORE INTO task_level_cost (task_type_id, niveau, travail) "
                "VALUES (?, ?, 1.0)", (tid, lvl))
            cat.conn.execute(
                "INSERT OR IGNORE INTO task_level_stats "
                "(task_type_id, niveau, ref_niveau) VALUES (?, ?, 'senior')",
                (tid, lvl))
        cat.conn.commit()
        return {"ok": True, "task_type_id": tid, "code": code, "cascade": [
            "scoring_task_types", "llm_task_type_score", "task_level_cost",
            "task_level_stats"]}
    except Exception as e:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def add_domaine(code: str, label: str = "", cat=None) -> Dict[str, Any]:
    """Ajoute un domaine, en CASCADE : scoring_domaines + llm_domaine_score."""
    cat = _cat(cat)
    if not code:
        return {"ok": False, "error": "code requis"}
    try:
        cat.conn.execute(
            "INSERT OR IGNORE INTO scoring_domaines (code, label) VALUES (?, ?)",
            (code, label or code))
        row = cat.conn.execute(
            "SELECT id FROM scoring_domaines WHERE code = ?", (code,)).fetchone()
        did = row["id"]
        cat.conn.execute("""
            INSERT OR IGNORE INTO llm_domaine_score (model_id, domaine_id)
            SELECT DISTINCT cm.id, ? FROM catalogue_models cm WHERE cm.model_key != ''
        """, (did,))
        cat.conn.commit()
        return {"ok": True, "domaine_id": did, "code": code, "cascade": [
            "scoring_domaines", "llm_domaine_score"]}
    except Exception as e:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def add_provider(ref: str, name: str = "", provider_type: str = "cloud",
                 api_type: str = "openai", cat=None) -> Dict[str, Any]:
    """Ajoute un provider au catalogue."""
    cat = _cat(cat)
    if not ref:
        return {"ok": False, "error": "ref requis"}
    if provider_type not in ("cloud", "local", "ollama", "builtin"):
        return {"ok": False, "error": f"provider_type invalide: {provider_type}"}
    try:
        cat.conn.execute(
            "INSERT OR IGNORE INTO catalogue_providers "
            "(ref, name, provider_type, api_type) VALUES (?, ?, ?, ?)",
            (ref, name or ref, provider_type, api_type))
        row = cat.conn.execute(
            "SELECT id FROM catalogue_providers WHERE ref = ?", (ref,)).fetchone()
        cat.conn.commit()
        return {"ok": True, "provider_id": row["id"], "ref": ref}
    except Exception as e:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def add_endpoint(provider_ref: str, endpoint_url: str, label: str = "",
                 api_type: str = "", cat=None) -> Dict[str, Any]:
    """Ajoute un endpoint à un provider."""
    cat = _cat(cat)
    if not provider_ref or not endpoint_url:
        return {"ok": False, "error": "provider_ref + endpoint_url requis"}
    try:
        prow = cat.conn.execute(
            "SELECT id, api_type FROM catalogue_providers WHERE ref = ?",
            (provider_ref,)).fetchone()
        if not prow:
            return {"ok": False, "error": f"provider introuvable: {provider_ref}"}
        cat.conn.execute(
            "INSERT OR IGNORE INTO provider_endpoints "
            "(provider_id, label, endpoint_url, api_type, is_default) "
            "VALUES (?, ?, ?, ?, 0)",
            (prow["id"], label or endpoint_url, endpoint_url,
             api_type or prow["api_type"]))
        row = cat.conn.execute(
            "SELECT endpoint_id FROM provider_endpoints "
            "WHERE provider_id = ? AND endpoint_url = ?",
            (prow["id"], endpoint_url)).fetchone()
        cat.conn.commit()
        return {"ok": True, "endpoint_id": row["id"]}
    except Exception as e:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def add_model(ref: str, model_key: str = "", name: str = "", cat=None) -> Dict[str, Any]:
    """Ajoute un modèle au catalogue, en CASCADE : scores par domaine/type."""
    cat = _cat(cat)
    if not ref:
        return {"ok": False, "error": "ref requis"}
    try:
        cat.conn.execute(
            "INSERT OR IGNORE INTO catalogue_models (ref, model_key, name) "
            "VALUES (?, ?, ?)", (ref, model_key or ref, name or model_key or ref))
        row = cat.conn.execute(
            "SELECT id FROM catalogue_models WHERE ref = ?", (ref,)).fetchone()
        if not row:
            return {"ok": False, "error": "insertion catalogue_models échouée"}
        mid = row["id"]
        # scores init (si tableau vide pour ce modèle)
        nd = cat.conn.execute(
            "SELECT COUNT(*) c FROM llm_domaine_score WHERE model_id = ?",
            (mid,)).fetchone()["c"]
        if nd == 0:
            cat.conn.execute("""
                INSERT OR IGNORE INTO llm_domaine_score (model_id, domaine_id)
                SELECT ?, id FROM scoring_domaines
            """, (mid,))
            cat.conn.execute("""
                INSERT OR IGNORE INTO llm_task_type_score (model_id, task_type_id)
                SELECT ?, id FROM scoring_task_types
            """, (mid,))
        cat.conn.commit()
        return {"ok": True, "model_id": mid, "ref": ref, "cascade": [
            "catalogue_models", "llm_domaine_score", "llm_task_type_score"]}
    except Exception as e:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def add_model_to_provider(provider_ref: str, model_ref: str,
                          provider_model_name: str = "", cat=None) -> Dict[str, Any]:
    """Lie un modèle à un provider, en CASCADE : provider_models + adresses
    + budget_final/cost (via le seed pipeline_budget)."""
    cat = _cat(cat)
    if not provider_ref or not model_ref:
        return {"ok": False, "error": "provider_ref + model_ref requis"}
    try:
        prow = cat.conn.execute(
            "SELECT id FROM catalogue_providers WHERE ref = ?",
            (provider_ref,)).fetchone()
        mrow = cat.conn.execute(
            "SELECT id FROM catalogue_models WHERE ref = ?", (model_ref,)).fetchone()
        if not prow or not mrow:
            return {"ok": False, "error": "provider ou model introuvable — "
                    "lancer add_provider/add_model d'abord"}
        pm_name = provider_model_name or model_ref
        cat.conn.execute("""
            INSERT OR IGNORE INTO provider_models
                (provider_id, model_id, provider_model_name, status)
            VALUES (?, ?, ?, 'active')
        """, (prow["id"], mrow["id"], pm_name))
        cat.conn.commit()
        # régénère les adresses + budgets/cost en cascade.
        try:
            from services.llm_allocation.address import ensure_addresses
            ensure_addresses(cat)
            from services.llm_usage.seed import seed_budgets
            seed_budgets(cat)
        except Exception:
            pass
        return {"ok": True, "cascade": [
            "provider_models", "provider_model_address", "adresse_runtime",
            "budget_final", "cost_final"]}
    except Exception as e:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}


def add_type_key(provider_ref: str, tag: str, cat=None) -> Dict[str, Any]:
    """Pose un type de clé (api_key_tag : free/plus/premium/'') sur les adresses
    d'un provider, en CASCADE sur budget_final/cost (via seed)."""
    cat = _cat(cat)
    if not provider_ref:
        return {"ok": False, "error": "provider_ref requis"}
    if tag is None:
        tag = ""
    try:
        n = cat.conn.execute(
            "UPDATE provider_model_address SET api_key_tag = ? "
            "WHERE provider_ref = ?", (tag, provider_ref)).rowcount
        cat.conn.execute(
            "UPDATE adresse_runtime SET api_key_tag = ? "
            "WHERE provider_ref = ?", (tag, provider_ref))
        cat.conn.commit()
        # régénère budgets/cost pour les tags touchés.
        try:
            from services.llm_usage.seed import seed_budgets
            seed_budgets(cat)
        except Exception:
            pass
        return {"ok": True, "provider": provider_ref, "tag": tag,
                "adresses_mises_a_jour": max(n, 0)}
    except Exception as e:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}