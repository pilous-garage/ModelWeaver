"""read — lectures du domaine info_llm pour le bridge.

Le bridge ne lit QUE ces tables (jamais local_catalogue). Sélections par
réf (indexées) + accès "comme on les aime" (ids stables).

Module sans token (lecture seule).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db


def get_provider(db: Db, ref: str) -> Optional[Dict[str, Any]]:
    return db.table("catalogue_providers").get({"ref": ref})


def get_model(db: Db, ref: str) -> Optional[Dict[str, Any]]:
    return db.table("catalogue_models").get({"ref": ref})


def capabilities(db: Db, model_id: int) -> List[Dict[str, Any]]:
    """Retourne la liste des capacités pour un modèle (model_id).
    Chaque entrée contient : capability, value (true/false/unknown),
    confidence (0..1), source_ref, last_info.
    """
    rows = db.table("model_capability").select(
        where={"model_id": model_id}, order_by="capability")
    return [dict(r) for r in rows]


def capability_conf(db: Db, model_id: int, cap: str) -> Optional[Dict[str, Any]]:
    """Retourne la capacité spécifique + confiance pour un modèle."""
    rows = db.table("model_capability").select(
        where={"model_id": model_id, "capability": cap}, order_by="id")
    if not rows:
        return None
    return dict(rows[0])


def decided_capability(db: Db, model_id: int, cap: str) -> Optional[bool]:
    """Décision binaire finale selon la confiance : >0.9→true, <0.1→false, else None."""
    row = db.table("model_capability").get(
        where={"model_id": model_id, "capability": cap})
    if not row:
        return None
    conf = row.get("confidence", 0.5)
    if conf > 0.9:
        return True
    if conf < 0.1:
        return False
    # inconnu → le bridge tentera un fallback
    return None


def provider_models_for(db: Db, provider_id: int) -> List[Dict[str, Any]]:
    return db.table("provider_models").select(
        where={"provider_id": provider_id}, order_by="name")


def model_key_mapping(db: Db, model_key: str) -> List[Dict[str, Any]]:
    """Toutes les provider_models servies pour un model_key canonique."""
    rows = db.table("provider_models_mapping").select(
        where={"model_key": model_key}, order_by="provider_model_id")
    ids = [r["provider_model_id"] for r in rows]
    if not ids:
        return []
    q = ("SELECT * FROM provider_models WHERE provider_model_id IN ("
         + ",".join("?" * len(ids)) + ")")
    return db.sql(q, ids)


def addresses(db: Db, provider_id: int = 0, model_key: str = "",
              api_key_type: str = "") -> List[Dict[str, Any]]:
    """Schéma d'adresses (statique, type-level), filtré par (provider,
    model_key, api_key_type). Aucun secret : la clé est résolue au runtime
    dans la table RAM `adress` (vault keyring)."""
    w: Dict[str, Any] = {}
    if provider_id:
        w["provider_id"] = provider_id
    if model_key:
        w["model_key"] = model_key
    if api_key_type:
        w["api_key_type"] = api_key_type
    return db.table("endpoint_apikeytype_model_adress").select(
        where=w or None, order_by="adress_id")


def key_types_for(db: Db, provider_id: int = 0,
                  endpoint_id: int = 0) -> List[Dict[str, Any]]:
    """Matrice endpoint ↔ api_key_type autorisés."""
    w: Dict[str, Any] = {}
    if provider_id:
        w["provider_id"] = provider_id
    if endpoint_id:
        w["endpoint_id"] = endpoint_id
    return db.table("provider_endpoint_api_key_type").select(
        where=w or None, order_by="api_key_type")


def costs_for(db: Db, model_id: int, key_tag: str = "") -> List[Dict[str, Any]]:
    w: Dict[str, Any] = {"model_id": model_id}
    if key_tag:
        w["key_tag"] = key_tag
    return db.table("cost_final").select(where=w, order_by="cost_id")


def resolve_adresse_id(db: Db, provider_ref: str, model_ref: str,
                       endpoint_ref: str = "", type_key: str = "") -> Optional[int]:
    """Résout (provider_ref, model_ref) → adress_id du schéma d'adresses HDD.

    Socle : le quadruple (provider_ref, endpoint_ref, model_endpoint_ref,
    type_key) — égalités EXACTES uniquement (pas de fuzzy matching). Les
    legs manquants sont déduits : endpoint par défaut du provider, type_key
    'default' alors 'unknown'. Retourne l'id stable de la matrice (lecture
    seule, aucun secret)."""
    if not provider_ref or not model_ref:
        return None
    conn = db._conn

    row = conn.execute(
        "SELECT provider_id FROM catalogue_providers WHERE ref = ?",
        (provider_ref,)).fetchone()
    if not row:
        return None
    provider_id = row["provider_id"]

    # ── endpoint_ref : explicite → défaut ('pid/default') → premier ───────
    endpoint_id = None
    if endpoint_ref:
        r = conn.execute(
            "SELECT endpoint_id FROM provider_endpoints WHERE ref = ?",
            (endpoint_ref,)).fetchone()
        endpoint_id = r["endpoint_id"] if r else None
    if endpoint_id is None:
        r = conn.execute(
            "SELECT endpoint_id FROM provider_endpoints "
            "WHERE provider_id = ? AND ref = ?",
            (provider_id, f"{provider_ref}/default")).fetchone()
        endpoint_id = r["endpoint_id"] if r else None
    if endpoint_id is None:
        r = conn.execute(
            "SELECT endpoint_id FROM provider_endpoints WHERE provider_id = ?",
            (provider_id,)).fetchone()
        endpoint_id = r["endpoint_id"] if r else None
    if endpoint_id is None:
        return None

    # ── model_endpoint_ref : égalités exactes (ref complet, nom, key) ─────
    stripped = model_ref.split("#", 1)[0]
    pmid = None
    r = conn.execute(
        "SELECT provider_model_id FROM provider_models "
        "WHERE provider_id = ? AND name = ?", (provider_id, model_ref)).fetchone()
    pmid = r["provider_model_id"] if r else None
    if pmid is None:
        r = conn.execute(
            "SELECT provider_model_id FROM provider_models "
            "WHERE provider_id = ? AND provider_model_name = ?",
            (provider_id, model_ref)).fetchone()
        pmid = r["provider_model_id"] if r else None
    if pmid is None:
        r = conn.execute("""
            SELECT pm.provider_model_id FROM provider_models_mapping m
            JOIN provider_models pm ON pm.provider_model_id = m.provider_model_id
            WHERE m.model_key = ? AND pm.provider_id = ?
        """, (model_ref, provider_id)).fetchone()
        pmid = r["provider_model_id"] if r else None
    if pmid is None and stripped != model_ref:
        r = conn.execute(
            "SELECT provider_model_id FROM provider_models "
            "WHERE provider_id = ? AND provider_model_name = ?",
            (provider_id, stripped)).fetchone()
        pmid = r["provider_model_id"] if r else None
    if pmid is None:
        return None

    # ── type_key : explicite → 'default' → 'unknown' → premier ────────────
    rows = conn.execute("""
        SELECT adress_id, api_key_type FROM endpoint_apikeytype_model_adress
        WHERE provider_id = ? AND endpoint_id = ? AND model_endpoint_id = ?
        ORDER BY CASE api_key_type WHEN 'default' THEN 0 WHEN 'unknown' THEN 1
                 ELSE 2 END, adress_id
    """, (provider_id, endpoint_id, pmid)).fetchall()
    if not rows:
        return None
    if type_key:
        for r in rows:
            if r["api_key_type"] == type_key:
                return r["adress_id"]
    return rows[0]["adress_id"]
