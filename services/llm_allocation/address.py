"""llm_allocation/address — répertoire provider_model_address.

Résout les clés textes (provider_ref/model_ref) vers un `adresse_id`
(provider × endpoint × provider_model) et remplit la table
`provider_model_address` du catalogue.

C'est la référence commune de TOUTES les tables de scoring/log/usage :
au lieu des textes, elles référenceront `adresse_id`.

Usage:
    from services.llm_allocation.address import ensure_addresses, resolve_address
    ensure_addresses()          # remplit la table (idempotent)
    aid = resolve_address("nvidia", "deepseek-ai/deepseek-v4-flash-0731")
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from modules.sql.catalogue_repo import CatalogueDB


def _provider_models_index(cat) -> Dict[str, int]:
    """{provider_model_name → provider_models.id} (toutes les formes)."""
    rows = cat.conn.execute(
        "SELECT id, provider_model_name FROM provider_models").fetchall()
    idx: Dict[str, int] = {}
    for r in rows:
        name = (r["provider_model_name"] or "").strip()
        if name:
            idx[name] = r["id"]
            # forme sans préfixe provider (dernier segment)
            if "/" in name:
                idx.setdefault(name.split("/")[-1], r["id"])
    return idx


def _normalize_name(name: str) -> str:
    """Normalise un model_ref pour le matching (casse, préfixe)."""
    n = (name or "").strip()
    n = n.split("/")[-1] if "/" in n else n
    return n.lower()


def _model_key_for(cat, model_ref: str) -> Tuple[Optional[int], str]:
    """Résout un model_ref (texte) → (model_id, model_key) via catalogue_models."""
    rows = cat.conn.execute(
        "SELECT id, model_key FROM catalogue_models WHERE ref = ? "
        "OR model_key = ? OR ref LIKE ? LIMIT 1",
        (model_ref, model_ref, f"%{model_ref}%")).fetchall()
    if not rows:
        # tentative par normalisation
        nm = _normalize_name(model_ref)
        rows = cat.conn.execute(
            "SELECT id, model_key FROM catalogue_models WHERE LOWER(model_key)=? "
            "OR LOWER(ref) LIKE ? LIMIT 1",
            (nm, f"%{nm}%")).fetchall()
    if not rows:
        return None, ""
    return rows[0]["id"], (rows[0]["model_key"] or "")


def _endpoint_for(cat, provider_id: int) -> Tuple[Optional[int], str]:
    """Endpoint par défaut d'un provider → (endpoint_id, url)."""
    row = cat.conn.execute(
        "SELECT endpoint_id, endpoint_url FROM provider_endpoints "
        "WHERE provider_id = ? AND is_default = 1 LIMIT 1",
        (provider_id,)).fetchone()
    if not row:
        row = cat.conn.execute(
            "SELECT endpoint_id, endpoint_url FROM provider_endpoints "
            "WHERE provider_id = ? LIMIT 1", (provider_id,)).fetchone()
    return (row["endpoint_id"], row["endpoint_url"]) if row else (None, "")


def ensure_addresses(cat=None) -> dict:
    """Remplit provider_model_address depuis provider_models (idempotent).

    Une adresse par (provider_id, endpoint_id, provider_model_id). Retourne
    {added, skipped, unresolved}."""
    cat = cat or CatalogueDB()
    pm_idx = _provider_models_index(cat)
    added = skipped = unresolved = 0
    unresolved_examples = []

    # les provider_models déjà adressés
    existing = {r["provider_model_id"]
                for r in cat.conn.execute(
                    "SELECT provider_model_id FROM provider_model_address")}

    rows = cat.conn.execute("""
        SELECT pm.id, pm.provider_id, pm.model_id, pm.provider_model_name,
               p.ref AS provider_ref
        FROM provider_models pm
        JOIN catalogue_providers p ON p.id = pm.provider_id
        ORDER BY pm.id
    """).fetchall()
    for r in rows:
        pmid = r["id"]
        if pmid in existing:
            continue
        provider_id = r["provider_id"]
        provider_ref = r["provider_ref"]
        pname = r["provider_model_name"] or ""
        model_id = r["model_id"]
        # model_key depuis catalogue_models
        model_key = ""
        if model_id:
            mrow = cat.conn.execute(
                "SELECT model_key FROM catalogue_models WHERE id = ?",
                (model_id,)).fetchone()
            model_key = (mrow["model_key"] or "") if mrow else ""
        endpoint_id, endpoint_url = _endpoint_for(cat, provider_id)
        try:
            cat.conn.execute("""
                INSERT OR IGNORE INTO provider_model_address
                    (provider_id, provider_ref, endpoint_id, endpoint_url,
                     model_id, model_key, provider_model_id, provider_model_name,
                     available, deprecated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0)
            """, (provider_id, provider_ref, endpoint_id, endpoint_url,
                  model_id, model_key, pmid, pname))
            added += 1
        except Exception:
            unresolved += 1
            if len(unresolved_examples) < 5:
                unresolved_examples.append(f"{provider_ref}/{pname}")
    cat.conn.commit()
    return {"added": added, "skipped": skipped,
            "unresolved": unresolved, "examples": unresolved_examples}


def resolve_address(provider_ref: str, model_ref: str,
                    cat=None) -> Optional[int]:
    """Résout (provider_ref, model_ref) → adresse_id (via la table)."""
    cat = cat or CatalogueDB()
    row = cat.conn.execute("""
        SELECT a.adresse_id FROM provider_model_address a
        WHERE a.provider_ref = ? AND a.provider_model_name = ? LIMIT 1
    """, (provider_ref, model_ref)).fetchone()
    if row:
        return row["adresse_id"]
    # tentative par model_key (variante de nom)
    _mid, mkey = _model_key_for(cat, model_ref)
    if mkey:
        row = cat.conn.execute("""
            SELECT a.adresse_id FROM provider_model_address a
            WHERE a.provider_ref = ? AND a.model_key = ? LIMIT 1
        """, (provider_ref, mkey)).fetchone()
        if row:
            return row["adresse_id"]
    return None


__all__ = ["ensure_addresses", "resolve_address", "_model_key_for",
           "_endpoint_for"]
