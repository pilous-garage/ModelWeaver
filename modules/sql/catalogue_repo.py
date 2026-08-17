#!/usr/bin/env python3
"""Catalogue — repositories et DB de la base catalogue.

Extrait de modules/sql/db.py (issue #11, découpage par domaine).
Contient : ProviderRepository, ModelRepository, KeyRepository,
LocalLLMRepository, LocalToolRepository, CommandRepository,
SystemStateRepository, TursoCatalogueDB, CatalogueDB.
"""

import json
import sqlite3
import uuid
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from modules.sql.schema import (
    _ref, _project_root, _default_local_db, _default_catalogue_db,
    _default_agents_db, _default_community_db, _default_user_db,
    _row_to_dict, _rows_to_list,
    _ensure_classes_outils_table, _add_column_if_missing,
    resolve_classe_id, _default_class_for_ref,
)
from modules.sql.migrations import MigrationManager
from services._common import mw_home


class ProviderRepository:
    """Fournisseurs API + liens provider_models."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, provider_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if provider_type:
            cur = self.conn.execute(
                "SELECT * FROM providers WHERE provider_type = ? ORDER BY name",
                (provider_type,)
            )
        else:
            cur = self.conn.execute("SELECT * FROM providers ORDER BY name")
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM providers WHERE ref = ?", (ref,))
        return _row_to_dict(cur.fetchone())

    def get_by_id(self, pid: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM providers WHERE id = ?", (pid,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        ref = data.get("ref")
        existing = None
        if ref:
            cur = self.conn.execute("SELECT id FROM providers WHERE ref = ?", (ref,))
            existing = cur.fetchone()

        if existing:
            self.conn.execute("""
                UPDATE providers SET name=?, provider_type=?, api_type=?,
                    website=?, limits_json=?, rate_limits_json=?,
                    next_reset_at=?, is_free_tier_provider=?,
                    updated_at=strftime('%s','now')
                WHERE id=?
            """, (
                data.get("name"), data.get("provider_type"),
                data.get("api_type"), data.get("website"),
                data.get("limits_json"), data.get("rate_limits_json"),
                data.get("next_reset_at"), data.get("is_free_tier_provider", 0),
                existing["id"]
            ))
            return existing["id"]

        cur = self.conn.execute("""
            INSERT INTO providers (ref, name, provider_type, api_type, website,
                limits_json, rate_limits_json, next_reset_at, is_free_tier_provider, catalogue_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ref or _ref("prov"), data.get("name"), data.get("provider_type", "cloud"),
            data.get("api_type"), data.get("website"),
            data.get("limits_json"), data.get("rate_limits_json"),
            data.get("next_reset_at"), data.get("is_free_tier_provider", 0),
            data.get("catalogue_ref")
        ))
        return cur.lastrowid

    def delete(self, ref: str) -> bool:
        cur = self.conn.execute("DELETE FROM providers WHERE ref = ?", (ref,))
        return cur.rowcount > 0

    def get_provider_models(self, provider_ref: str) -> List[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT pm.*, m.ref as model_ref, m.name as model_name, m.developer
            FROM provider_models pm
            JOIN providers p ON p.id = pm.provider_id
            JOIN models m ON m.id = pm.model_id
            WHERE p.ref = ?
            ORDER BY m.name
        """, (provider_ref,))
        return _rows_to_list(cur.fetchall())

    def get_full_details(self, model_ref: Optional[str] = None,
                         provider_ref: Optional[str] = None) -> List[Dict[str, Any]]:
        query = """
            SELECT p.ref as provider_ref, p.name as provider_name, p.provider_type,
                   p.api_type, p.limits_json as provider_limits,
                   m.ref as model_ref, m.name as model_name, m.developer,
                   m.architecture, m.parameter_count, m.modality, m.target_use,
                   pm.provider_model_name, pm.context_window_tokens,
                   pm.cost_per_input_token, pm.cost_per_output_token, pm.cost_billing,
                   pm.pricing_rules_json, pm.limits_json, pm.rate_limits_json,
                   pm.status as model_status, pm.next_reset_at
            FROM provider_models pm
            JOIN providers p ON p.id = pm.provider_id
            JOIN models m ON m.id = pm.model_id
            WHERE 1=1
        """
        params = []
        if model_ref:
            query += " AND m.ref = ?"
            params.append(model_ref)
        if provider_ref:
            query += " AND p.ref = ?"
            params.append(provider_ref)
        query += " ORDER BY p.name, m.name"

        cur = self.conn.execute(query, params)
        return _rows_to_list(cur.fetchall())


class ModelRepository:
    """Modèles purs (indépendants des fournisseurs)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, developer: Optional[str] = None,
                 modality: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = []
        params = []
        if developer:
            clauses.append("developer = ?")
            params.append(developer)
        if modality:
            clauses.append("modality = ?")
            params.append(modality)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cur = self.conn.execute(f"SELECT * FROM models{where} ORDER BY name", params)
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM models WHERE ref = ?", (ref,))
        return _row_to_dict(cur.fetchone())

    def get_by_id(self, mid: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM models WHERE id = ?", (mid,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        ref = data.get("ref")
        existing = None
        if ref:
            cur = self.conn.execute("SELECT id FROM models WHERE ref = ?", (ref,))
            existing = cur.fetchone()

        if existing:
            self.conn.execute("""
                UPDATE models SET name=?, developer=?, release_year=?, architecture=?,
                    parameter_count=?, modality=?, target_use=?, license=?,
                    is_open_weights=?, parent_model_id=?, metadata_json=?,
                    updated_at=strftime('%s','now')
                WHERE id=?
            """, (
                data.get("name"), data.get("developer"), data.get("release_year"),
                data.get("architecture"), data.get("parameter_count"),
                data.get("modality"), data.get("target_use"), data.get("license"),
                data.get("is_open_weights", 0), data.get("parent_model_id"),
                data.get("metadata_json"), existing["id"]
            ))
            return existing["id"]

        cur = self.conn.execute("""
            INSERT INTO models (ref, name, developer, release_year, architecture,
                parameter_count, modality, target_use, license, is_open_weights,
                parent_model_id, catalogue_ref, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ref or _ref("model"), data.get("name"), data.get("developer"),
            data.get("release_year"), data.get("architecture"),
            data.get("parameter_count"), data.get("modality"),
            data.get("target_use"), data.get("license"),
            data.get("is_open_weights", 0), data.get("parent_model_id"),
            data.get("catalogue_ref"), data.get("metadata_json")
        ))
        return cur.lastrowid

    def search(self, query: str, modality: Optional[str] = None) -> List[Dict[str, Any]]:
        """Recherche textuelle sur les noms, refs et développeurs."""
        clauses = ["(name LIKE ? OR ref LIKE ? OR developer LIKE ?)"]
        params = [f"%{query}%", f"%{query}%", f"%{query}%"]
        if modality:
            clauses.append("modality = ?")
            params.append(modality)
        where = " WHERE " + " AND ".join(clauses)
        cur = self.conn.execute(f"SELECT * FROM models{where} ORDER BY name LIMIT 50", params)
        return _rows_to_list(cur.fetchall())

    def link_provider(self, provider_id: int, model_id: int,
                      provider_model_name: str, extra: Optional[Dict] = None) -> int:
        """Crée ou met à jour un lien provider→modèle."""
        cur = self.conn.execute(
            "SELECT id FROM provider_models WHERE provider_id = ? AND model_id = ?",
            (provider_id, model_id)
        )
        existing = cur.fetchone()
        if existing:
            self.conn.execute("""
                UPDATE provider_models SET provider_model_name=?, context_window_tokens=?,
                    max_output_tokens=?, cost_per_input_token=?, cost_per_output_token=?,
                    cost_billing=?, pricing_rules_json=?, limits_json=?,
                    rate_limits_json=?, metadata_json=?, available=?, updated_at=strftime('%s','now')
                WHERE id=?
            """, (
                provider_model_name,
                (extra or {}).get("context_window_tokens"),
                (extra or {}).get("max_output_tokens"),
                (extra or {}).get("cost_per_input_token"),
                (extra or {}).get("cost_per_output_token"),
                (extra or {}).get("cost_billing"),
                (extra or {}).get("pricing_rules_json"),
                (extra or {}).get("limits_json"),
                (extra or {}).get("rate_limits_json"),
                (extra or {}).get("metadata_json"),
                (extra or {}).get("available", 1),
                existing["id"]
            ))
            return existing["id"]

        cur = self.conn.execute("""
            INSERT INTO provider_models (provider_id, model_id, provider_model_name,
                context_window_tokens, max_output_tokens, cost_per_input_token,
                cost_per_output_token, cost_billing, pricing_rules_json,
                limits_json, rate_limits_json, metadata_json, available)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            provider_id, model_id, provider_model_name,
            (extra or {}).get("context_window_tokens"),
            (extra or {}).get("max_output_tokens"),
            (extra or {}).get("cost_per_input_token"),
            (extra or {}).get("cost_per_output_token"),
            (extra or {}).get("cost_billing"),
            (extra or {}).get("pricing_rules_json"),
            (extra or {}).get("limits_json"),
            (extra or {}).get("rate_limits_json"),
            (extra or {}).get("metadata_json"),
            (extra or {}).get("available", 1)
        ))
        return cur.lastrowid


class ModelCapaciteRepository:
    """Capacités d'un modèle PAR (endpoint, provider) — expérience + confiance.

    Chaque capacité est un tuple (model_id, endpoint_id, provider_id,
    capability, cap_type). Deux types :
      - 'bool'  : agentic, vision, supports_chat, streaming, function_calling,
                  reasoning → confiance 0-1 (monte/baisse selon les observations)
      - 'range' : context_window, max_input, max_output → min/max observés

    La capacité OFFICIELLE du modèle (catalogue_models / model_capabilities)
    est la BORNE HAUTE ; l'expérience ici ne peut que la restreindre
    (un endpoint n'a jamais PLUS de capacité que le modèle).
    """

    # Capacités booléennes connues.
    BOOL_CAPS = ("agentic", "agentic-translation", "vision", "supports_chat",
                 "streaming", "function_calling", "reasoning")
    # Capacités de plage connues (tokens).
    RANGE_CAPS = ("context_window", "max_input", "max_output")

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ── Lecture ──

    def get(self, model_id: int, endpoint_id: int, provider_id: int,
            capability: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT * FROM model_endpoint_provider_capacite
            WHERE model_id = ? AND endpoint_id = ? AND provider_id = ?
              AND capability = ?
        """, (model_id, endpoint_id, provider_id, capability))
        return _row_to_dict(cur.fetchone())

    def list_for_model(self, model_id: int,
                       endpoint_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if endpoint_id is not None:
            cur = self.conn.execute("""
                SELECT * FROM model_endpoint_provider_capacite
                WHERE model_id = ? AND endpoint_id = ?
                ORDER BY capability
            """, (model_id, endpoint_id))
        else:
            cur = self.conn.execute("""
                SELECT * FROM model_endpoint_provider_capacite
                WHERE model_id = ? ORDER BY endpoint_id, capability
            """, (model_id,))
        return _rows_to_list(cur.fetchall())

    def list_by_endpoint(self, endpoint_id: int,
                         capability: Optional[str] = None) -> List[Dict[str, Any]]:
        if capability:
            cur = self.conn.execute("""
                SELECT * FROM model_endpoint_provider_capacite
                WHERE endpoint_id = ? AND capability = ?
                ORDER BY model_id
            """, (endpoint_id, capability))
        else:
            cur = self.conn.execute("""
                SELECT * FROM model_endpoint_provider_capacite
                WHERE endpoint_id = ? ORDER BY model_id, capability
            """, (endpoint_id,))
        return _rows_to_list(cur.fetchall())

    # ── Écriture ──

    def observe_bool(self, model_id: int, endpoint_id: int, provider_id: int,
                     capability: str, ok: bool, source: str = "experience",
                     strength: float = 1.0) -> None:
        """Enregistre une observation booléenne de capacité.

        `ok=True` → la capacité a fonctionné (confidence monte) ;
        `ok=False` → elle a échoué (confidence baisse).
        `strength` : pondération de l'observation (0-1, défaut 1).
        Le score de confiance est un taux lissé : chaque observation rapproche
        la confiance de 1 (succès) ou 0 (échec), pondérée par `strength`.
        """
        assert capability in self.BOOL_CAPS, f"capacité bool inconnue: {capability}"
        existing = self.get(model_id, endpoint_id, provider_id, capability)
        if existing is None:
            self.conn.execute("""
                INSERT OR IGNORE INTO model_endpoint_provider_capacite
                    (model_id, endpoint_id, provider_id, capability, cap_type,
                     confidence, yes_count, no_count, source,
                     last_observed_at, updated_at)
                VALUES (?, ?, ?, ?, 'bool', 0.5, 0, 0, ?,
                        strftime('%s','now'), strftime('%s','now'))
            """, (model_id, endpoint_id, provider_id, capability, source))
            existing = self.get(model_id, endpoint_id, provider_id, capability)
        conf = float(existing["confidence"] or 0.5)
        target = 1.0 if ok else 0.0
        # Lissage exponentiel : strength=0.5 → chaque observation rapproche la
        # confiance de moitié vers la cible (2 succès depuis 0.5 → 0.875).
        # Une valeur strength=1.0 écraserait (pas de mémoire) — on borne à 0.7.
        alpha = max(0.05, min(0.7, strength))
        conf = conf + alpha * (target - conf)
        conf = max(0.0, min(1.0, conf))
        if ok:
            self.conn.execute("""
                UPDATE model_endpoint_provider_capacite SET
                    confidence = ?, yes_count = yes_count + 1,
                    source = ?, last_observed_at = strftime('%s','now'),
                    updated_at = strftime('%s','now')
                WHERE model_id = ? AND endpoint_id = ? AND provider_id = ?
                  AND capability = ?
            """, (conf, source, model_id, endpoint_id, provider_id, capability))
        else:
            self.conn.execute("""
                UPDATE model_endpoint_provider_capacite SET
                    confidence = ?, no_count = no_count + 1,
                    source = ?, last_observed_at = strftime('%s','now'),
                    updated_at = strftime('%s','now')
                WHERE model_id = ? AND endpoint_id = ? AND provider_id = ?
                  AND capability = ?
            """, (conf, source, model_id, endpoint_id, provider_id, capability))

    def observe_range(self, model_id: int, endpoint_id: int, provider_id: int,
                      capability: str, value: int, source: str = "experience",
                      confidence: float = 1.0) -> None:
        """Enregistre une observation de plage (context_window / max_output…).

        Étend min/max observés. `confidence` pondère la fiabilité de la mesure
        (1.0 = mesuré par l'API, plus faible = estimé).
        """
        assert capability in self.RANGE_CAPS, f"capacité range inconnue: {capability}"
        existing = self.get(model_id, endpoint_id, provider_id, capability)
        if existing is None:
            self.conn.execute("""
                INSERT INTO model_endpoint_provider_capacite
                    (model_id, endpoint_id, provider_id, capability, cap_type,
                     confidence, min_value, max_value, observations, source,
                     last_observed_at, updated_at)
                VALUES (?, ?, ?, ?, 'range', ?, ?, ?, 1, ?,
                        strftime('%s','now'), strftime('%s','now'))
            """, (model_id, endpoint_id, provider_id, capability,
                  confidence, value, value, source))
            return
        mn = existing.get("min_value")
        mx = existing.get("max_value")
        new_min = value if (mn is None or value < mn) else mn
        new_max = value if (mx is None or value > mx) else mx
        self.conn.execute("""
            UPDATE model_endpoint_provider_capacite SET
                min_value = ?, max_value = ?, observations = observations + 1,
                confidence = ?, source = ?,
                last_observed_at = strftime('%s','now'),
                updated_at = strftime('%s','now')
            WHERE model_id = ? AND endpoint_id = ? AND provider_id = ?
              AND capability = ?
        """, (new_min, new_max, confidence, source,
              model_id, endpoint_id, provider_id, capability))

    def set_manual(self, model_id: int, endpoint_id: int, provider_id: int,
                   capability: str, cap_type: str, value: Any) -> None:
        """Fixe une capacité à la main (source='manual', confiance forte)."""
        assert cap_type in ("bool", "range")
        existing = self.get(model_id, endpoint_id, provider_id, capability)
        if cap_type == "bool":
            conf = 0.9 if value else 0.1
            if existing is None:
                self.conn.execute("""
                    INSERT INTO model_endpoint_provider_capacite
                        (model_id, endpoint_id, provider_id, capability, cap_type,
                         confidence, source, updated_at)
                    VALUES (?, ?, ?, ?, 'bool', ?, 'manual', strftime('%s','now'))
                """, (model_id, endpoint_id, provider_id, capability, conf))
            else:
                self.conn.execute("""
                    UPDATE model_endpoint_provider_capacite SET
                        confidence = ?, source = 'manual',
                        updated_at = strftime('%s','now')
                    WHERE model_id = ? AND endpoint_id = ? AND provider_id = ?
                      AND capability = ?
                """, (conf, model_id, endpoint_id, provider_id, capability))
        else:
            v = int(value)
            if existing is None:
                self.conn.execute("""
                    INSERT INTO model_endpoint_provider_capacite
                        (model_id, endpoint_id, provider_id, capability, cap_type,
                         confidence, min_value, max_value, observations, source,
                         updated_at)
                    VALUES (?, ?, ?, ?, 'range', 0.9, ?, ?, 1, 'manual',
                            strftime('%s','now'))
                """, (model_id, endpoint_id, provider_id, capability, v, v))
            else:
                self.conn.execute("""
                    UPDATE model_endpoint_provider_capacite SET
                        confidence = 0.9, min_value = ?, max_value = ?,
                        observations = 1, source = 'manual',
                        updated_at = strftime('%s','now')
                    WHERE model_id = ? AND endpoint_id = ? AND provider_id = ?
                      AND capability = ?
                """, (v, v, model_id, endpoint_id, provider_id, capability))

    def resolve_bool(self, model_id: int, endpoint_id: int, provider_id: int,
                     capability: str, default: bool = False) -> bool:
        """État booléen dérivé : vrai si confidence >= 0.7.

        Inconnu (0.5) → `default`. On ne prend JAMAIS un 'non' pour acquis à
        basse confiance (un modèle peut être bridé par un endpoint mais pas
        l'autre) : seuls les échecs répétés font tomber sous 0.3.
        """
        row = self.get(model_id, endpoint_id, provider_id, capability)
        if row is None:
            return default
        conf = float(row.get("confidence") or 0.5)
        if conf >= 0.7:
            return True
        if conf <= 0.3:
            return False
        return default


class KeyRepository:
    """Clés API."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, identity: Optional[str] = None,
                 tag: Optional[str] = None,
                 health_status: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = []
        params = []
        if identity:
            clauses.append("identity = ?")
            params.append(identity)
        if tag:
            clauses.append("tag = ?")
            params.append(tag)
        if health_status:
            clauses.append("health_status = ?")
            params.append(health_status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cur = self.conn.execute(f"""
            SELECT ak.*, p.ref as provider_ref, p.name as provider_name
            FROM api_keys ak
            JOIN providers p ON p.id = ak.provider_id
            {where}
            ORDER BY ak.identity, p.name
        """, params)
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT ak.*, p.ref as provider_ref, p.name as provider_name
            FROM api_keys ak
            JOIN providers p ON p.id = ak.provider_id
            WHERE ak.ref = ?
        """, (ref,))
        return _row_to_dict(cur.fetchone())

    def get_for_provider(self, provider_ref: str, identity: str = "default") -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT ak.* FROM api_keys ak
            JOIN providers p ON p.id = ak.provider_id
            WHERE p.ref = ? AND ak.identity = ?
            AND ak.locked = 0
            AND ak.health_status IN ('unknown', 'ok', 'degraded')
            ORDER BY ak.health_status = 'ok' DESC, ak.health_status = 'unknown' DESC
            LIMIT 1
        """, (provider_ref, identity))
        return _row_to_dict(cur.fetchone())

    def get_any_for_provider(self, provider_ref: str, identity: str = "default") -> Optional[Dict[str, Any]]:
        """Comme get_for_provider mais ignore le verrou (détecte l'existence)."""
        cur = self.conn.execute("""
            SELECT ak.* FROM api_keys ak
            JOIN providers p ON p.id = ak.provider_id
            WHERE p.ref = ? AND ak.identity = ?
            ORDER BY ak.health_status = 'ok' DESC, ak.health_status = 'unknown' DESC
            LIMIT 1
        """, (provider_ref, identity))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> str:
        ref = data.get("ref") or _ref()
        cur = self.conn.execute("""
            INSERT INTO api_keys (ref, identity, provider_id, key_value, key_display, tag, grade,
                health_status, expiration_date, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ref, data.get("identity", "default"),
            data["provider_id"], data["key_value"],
            data.get("key_display"),
            data.get("tag", "paid"), data.get("grade"),
            data.get("health_status", "unknown"),
            data.get("expiration_date"), data.get("metadata_json")
        ))
        return ref

    def update_health(self, ref: str, status: str, error: Optional[str] = None) -> None:
        self.conn.execute("""
            UPDATE api_keys SET health_status=?, last_error=?,
                last_tested_at=strftime('%s','now'),
                error_count = CASE WHEN ? IS NOT NULL THEN error_count + 1 ELSE error_count END,
                updated_at=strftime('%s','now')
            WHERE ref=?
        """, (status, error, error, ref))

    def update(self, ref: str, tag: Optional[str] = None,
               grade: Optional[str] = None,
               metadata_json: Optional[str] = None) -> None:
        fields, params = [], []
        if tag is not None:
            fields.append("tag = ?"); params.append(tag)
        if grade is not None:
            fields.append("grade = ?"); params.append(grade)
        if metadata_json is not None:
            fields.append("metadata_json = ?"); params.append(metadata_json)
        if not fields:
            return
        fields.append("updated_at = strftime('%s','now')")
        params.append(ref)
        self.conn.execute(
            f"UPDATE api_keys SET {', '.join(fields)} WHERE ref = ?", params)

    def delete(self, ref: str) -> bool:
        cur = self.conn.execute("DELETE FROM api_keys WHERE ref = ?", (ref,))
        return cur.rowcount > 0

    def set_lock(self, ref: str, locked: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE api_keys SET locked = ?, updated_at = strftime('%s','now') WHERE ref = ?",
            (1 if locked else 0, ref))
        return cur.rowcount > 0


class LocalLLMRepository:
    """LLM téléchargés localement."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            cur = self.conn.execute(
                "SELECT * FROM local_llms WHERE status = ? ORDER BY name", (status,)
            )
        else:
            cur = self.conn.execute("SELECT * FROM local_llms ORDER BY name")
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM local_llms WHERE ref = ?", (ref,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        ref = data.get("ref")
        existing = None
        if ref:
            cur = self.conn.execute("SELECT id FROM local_llms WHERE ref = ?", (ref,))
            existing = cur.fetchone()
        if existing:
            self.conn.execute("""
                UPDATE local_llms SET name=?, ram_required_mb=?, chipset=?,
                    launch_command=?, api_base_url=?, context_window_tokens=?,
                    capabilities_json=?, status=?, parameters_json=?,
                    updated_at=strftime('%s','now')
                WHERE id=?
            """, (
                data.get("name"), data.get("ram_required_mb"),
                data.get("chipset"), data.get("launch_command"),
                data.get("api_base_url"), data.get("context_window_tokens"),
                data.get("capabilities_json"), data.get("status"),
                data.get("parameters_json"), existing["id"]
            ))
            return existing["id"]
        cur = self.conn.execute("""
            INSERT INTO local_llms (ref, name, model_id, ram_required_mb, chipset,
                launch_command, api_base_url, context_window_tokens,
                capabilities_json, status, parameters_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ref or _ref("llm"), data.get("name"), data.get("model_id"),
            data.get("ram_required_mb"), data.get("chipset"),
            data.get("launch_command"), data.get("api_base_url"),
            data.get("context_window_tokens"), data.get("capabilities_json"),
            data.get("status", "not_downloaded"), data.get("parameters_json")
        ))
        return cur.lastrowid


class LocalToolRepository:
    """Gestion des outils locaux (local_outils / local_versions / local_installs).

    Calquée sur la même structure que le catalogue (outils/versions/recettes)
    pour permettre plusieurs versions d'un même outil installées par des managers
    différents (ex: litellm pip + conda).
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self) -> List[Dict[str, Any]]:
        """Liste tous les outils installés localement."""
        cur = self.conn.execute("""
            SELECT lo.outil_ref, lo.nom, lo.tool_type, c.ref AS classe_ref, c.nom AS classe_nom,
                   lv.nom_version,
                   li.os, li.arch, li.manager, li.package,
                   li.version_installee, li.install_path, li.status
            FROM local_outils lo
            LEFT JOIN classes_outils c ON c.classe_id = lo.classe_outil_id
            JOIN local_versions lv ON lv.local_outil_id = lo.local_outil_id
            JOIN local_installs li ON li.local_version_id = lv.local_version_id
            ORDER BY lo.nom
        """)
        return _rows_to_list(cur.fetchall())

    def get(self, outil_ref: str) -> Optional[Dict[str, Any]]:
        """Dernière installation d'un outil."""
        cur = self.conn.execute("""
            SELECT lo.*, c.ref AS classe_ref, c.nom AS classe_nom, lv.nom_version, li.*
            FROM local_outils lo
            LEFT JOIN classes_outils c ON c.classe_id = lo.classe_outil_id
            JOIN local_versions lv ON lv.local_outil_id = lo.local_outil_id
            JOIN local_installs li ON li.local_version_id = lv.local_version_id
            WHERE lo.outil_ref = ?
            ORDER BY li.ts DESC LIMIT 1
        """, (outil_ref,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any], classe_ref: Optional[str] = None) -> int:
        """Enregistre une installation locale.

        Crée local_outils et local_versions si inexistants.
        L'upsert de local_installs utilise la clé (version, manager, os, arch).

        classe_ref : ref de la classe métier (ex: 'agent'). Si absent, on
        déduit depuis le ref de l'outil via le mapping par défaut.
        """
        ref = data.get("outil_ref") or data.get("ref")
        if not ref:
            raise ValueError("outil_ref requis")
        _ensure_classes_outils_table(self.conn)
        if classe_ref is None:
            classe_ref = data.get("classe") or _default_class_for_ref(ref)
        classe_id = resolve_classe_id(self.conn, classe_ref)
        # local_outils
        self.conn.execute("""
            INSERT INTO local_outils (outil_ref, nom, tool_type, classe_outil_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(outil_ref) DO UPDATE SET
                nom=excluded.nom,
                tool_type=COALESCE(excluded.tool_type, local_outils.tool_type),
                classe_outil_id=COALESCE(excluded.classe_outil_id, local_outils.classe_outil_id)
        """, (ref, data.get("nom", ref), data.get("tool_type"), classe_id))
        lid = self.conn.execute(
            "SELECT local_outil_id FROM local_outils WHERE outil_ref=?", (ref,)
        ).fetchone()[0]
        # local_versions
        ver = data.get("nom_version") or data.get("version") or "latest"
        self.conn.execute("""
            INSERT INTO local_versions (local_outil_id, nom_version)
            VALUES (?, ?)
            ON CONFLICT(local_outil_id, nom_version) DO NOTHING
        """, (lid, ver))
        vid = self.conn.execute(
            "SELECT local_version_id FROM local_versions WHERE local_outil_id=? AND nom_version=?",
            (lid, ver)).fetchone()[0]
        # local_installs
        cur = self.conn.execute(
            "SELECT install_id FROM local_installs WHERE local_version_id=? AND manager=? AND os=? AND arch=?",
            (vid, data.get("manager"), data.get("os", self._os_key()), data.get("arch", self._arch_key())))
        existing = cur.fetchone()
        if existing:
            self.conn.execute("""
                UPDATE local_installs
                SET version_installee=?, install_path=?, package=?, status=?,
                    ts=strftime('%s','now')
                WHERE install_id=?
            """, (data.get("version_installee") or data.get("version"),
                  data.get("install_path"), data.get("package"),
                  data.get("status", "installed"), existing["install_id"]))
            return existing["install_id"]
        cur = self.conn.execute("""
            INSERT INTO local_installs
                (local_version_id, os, arch, manager, package, version_installee, install_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (vid,
              data.get("os", self._os_key()), data.get("arch", self._arch_key()),
              data.get("manager"), data.get("package"),
              data.get("version_installee") or data.get("version"),
              data.get("install_path"), data.get("status", "installed")))
        return cur.lastrowid

    def remove(self, ref: str) -> bool:
        """Supprime toutes les installations locales d'un outil."""
        cur = self.conn.execute("""
            DELETE FROM local_installs WHERE local_version_id IN (
                SELECT local_version_id FROM local_versions
                WHERE local_outil_id=(SELECT local_outil_id FROM local_outils WHERE outil_ref=?))
        """, (ref,))
        return cur.rowcount > 0

    @staticmethod
    def _os_key() -> str:
        import platform; return platform.system().lower()

    @staticmethod
    def _arch_key() -> str:
        import platform
        m = platform.machine().lower()
        m = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(m, m)
        return m


class CommandRepository:
    """Commandes utiles aux IA."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, command_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if command_type:
            cur = self.conn.execute(
                "SELECT * FROM commands WHERE command_type = ? ORDER BY name",
                (command_type,)
            )
        else:
            cur = self.conn.execute("SELECT * FROM commands ORDER BY name")
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM commands WHERE ref = ?", (ref,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        cur = self.conn.execute("""
            INSERT INTO commands (ref, name, description, command_type, catalogue_ref)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data.get("ref") or _ref("cmd"), data.get("name"),
            data.get("description"), data.get("command_type"),
            data.get("catalogue_ref")
        ))
        return cur.lastrowid


# ──────────────────────────────────────────────
#  Tool Classes Repository
# ──────────────────────────────────────────────

class SystemStateRepository:
    """État actuel du système (OS, Archi, Managers)."""
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def save(self, state: Dict[str, Any]) -> None:
        self.conn.execute("""
            INSERT INTO system_state (id, os, architecture, os_version, detected_managers, updated_at)
            VALUES (1, ?, ?, ?, ?, strftime('%s','now'))
            ON CONFLICT(id) DO UPDATE SET
                os=excluded.os, architecture=excluded.architecture,
                os_version=excluded.os_version, detected_managers=excluded.detected_managers,
                updated_at=excluded.updated_at
        """, (
            state.get("os"), state.get("architecture"), state.get("os_version"),
            ",".join(state.get("detected_managers", []))
        ))

    def get(self) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM system_state WHERE id = 1")
        return _row_to_dict(cur.fetchone())



class TursoCatalogueDB:
    """Catalogue distant hébergé sur Turso (libSQL).
    
    Permet des requêtes filtrées sans charger toute la DB en local.
    """
    def __init__(self):
        self.url = os.getenv("TURSO_URL")
        self.token = os.getenv("TURSO_TOKEN")
        if not self.url or not self.token:
            raise RuntimeError("TURSO_URL et TURSO_TOKEN doivent être définis dans .env")
        
        try:
            import libsql
            self.client = libsql.connect(self.url, auth_token=self.token)
        except Exception as e:
            raise RuntimeError(f"Échec de connexion Turso: {e}")

    def get_tool(self, ref: str) -> Optional[Dict[str, Any]]:
        """Récupère un outil depuis le catalogue distant."""
        try:
            cur = self.client.execute("SELECT * FROM catalogue_outils WHERE ref = ?", (ref,))
            row = cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            return None
        except Exception as e:
            print(f"❌ Erreur Turso (get_tool): {e}")
            return None

    def list_tools(self, tool_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Liste les outils avec filtre optionnel."""
        query = "SELECT * FROM catalogue_outils"
        params = []
        if tool_type:
            query += " WHERE tool_type = ?"
            params.append(tool_type)
        query += " ORDER BY nom"
        
        try:
            cur = self.client.execute(query, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            print(f"❌ Erreur Turso (list_tools): {e}")
            return []

    def _commit(self):
        try:
            self.client.commit()
        except Exception:
            pass

    def upsert_tool(self, tool_data: Dict[str, Any]) -> bool:
        """Ajoute ou met à jour un outil dans le catalogue distant.

        Renseigne classe_outil_id à partir de la classe métier fournie
        (tool_data['classe'] ou tool_data['classe_ref']), sinon déduite
        du ref via le mapping par défaut.
        """
        try:
            _ensure_classes_outils_table(self.client)
            classe_ref = tool_data.get("classe") or tool_data.get("classe_ref")
            if not classe_ref:
                classe_ref = _default_class_for_ref(tool_data.get("ref", ""))
            classe_id = resolve_classe_id(self.client, classe_ref)
        except Exception:
            classe_id = None
        sql = """
        INSERT INTO catalogue_outils (ref, nom, description, tool_type, classe_outil_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ref) DO UPDATE SET
            nom=excluded.nom, description=excluded.description,
            tool_type=excluded.tool_type,
            classe_outil_id=COALESCE(excluded.classe_outil_id, catalogue_outils.classe_outil_id)
        """
        params = (
            tool_data.get("ref"), tool_data.get("name") or tool_data.get("nom"),
            tool_data.get("description"), tool_data.get("tool_type"), classe_id,
        )
        try:
            self.client.execute(sql, params)
            self._commit()
            return True
        except Exception as e:
            print(f"❌ Erreur Turso (upsert_tool): {e}")
            return False

    def get_recipe_content(self, ref: str, mgr: str, os_key: str, arch_key: str) -> Optional[str]:
        """Récupère le contenu YAML d'une recette depuis le catalogue distant."""
        sql = """
        SELECT r.content FROM catalogue_recettes r
        JOIN catalogue_versions v ON v.version_id = r.version_id
        JOIN catalogue_outils o ON o.outil_id = v.outil_id
        WHERE o.ref = ? AND r.manager = ?
          AND r.os IN (?, 'all') AND r.arch IN (?, 'all')
        ORDER BY (r.os = ?) DESC, (r.arch = ?) DESC, r.confidence DESC
        LIMIT 1
        """
        try:
            cur = self.client.execute(sql, (ref, mgr, os_key, arch_key, os_key, arch_key))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            print(f"❌ Erreur Turso (get_recipe_content): {e}")
            return None

    def upsert_version(self, outil_id: int, nom_version: str, description: Optional[str] = None) -> Optional[int]:
        """Ajoute une version dans le catalogue distant et retourne version_id."""
        sql = """
        INSERT INTO catalogue_versions (outil_id, nom_version, description)
        VALUES (?, ?, ?)
        ON CONFLICT(outil_id, nom_version) DO UPDATE SET
            description=COALESCE(excluded.description, catalogue_versions.description)
        """
        try:
            self.client.execute(sql, (outil_id, nom_version, description))
            self._commit()
            cur = self.client.execute(
                "SELECT version_id FROM catalogue_versions WHERE outil_id=? AND nom_version=?",
                (outil_id, nom_version))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            print(f"❌ Erreur Turso (upsert_version): {e}")
            return None

    def upsert_recipe(self, version_id: int, os: str, arch: str, manager: str,
                      package: Optional[str] = None, content: Optional[str] = None,
                      confidence: float = 1.0) -> bool:
        """Ajoute ou met à jour une recette dans le catalogue distant."""
        sql = """
        INSERT INTO catalogue_recettes (version_id, os, arch, manager, package, content, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(version_id, os, arch, manager) DO UPDATE SET
            package=COALESCE(excluded.package, catalogue_recettes.package),
            content=COALESCE(excluded.content, catalogue_recettes.content),
            confidence=excluded.confidence
        """
        try:
            self.client.execute(sql, (version_id, os, arch, manager, package, content, confidence))
            self._commit()
            return True
        except Exception as e:
            print(f"❌ Erreur Turso (upsert_recipe): {e}")
            return False

    def upsert_popularity(self, outil_id: int, nb_install: int = 0, nb_desinstall: int = 0) -> bool:
        """Ajoute ou met à jour la popularité d'un outil."""
        sql = """
        INSERT INTO outils_popularite (outil_id, nb_install, nb_desinstall)
        VALUES (?, ?, ?)
        ON CONFLICT(outil_id) DO UPDATE SET
            nb_install=excluded.nb_install,
            nb_desinstall=excluded.nb_desinstall,
            updated_at=strftime('%s','now')
        """
        try:
            self.client.execute(sql, (outil_id, nb_install, nb_desinstall))
            self._commit()
            return True
        except Exception as e:
            print(f"❌ Erreur Turso (upsert_popularity): {e}")
            return False


def _cols(cur) -> list:
    """Extrait les noms de colonnes d'un cursor."""
    return [d[0] for d in cur.description] if cur.description else []


def fetch_remote_to_local():
    """Importe le catalogue distant (Turso) vers le local (catalogue.db).

    Bouton manuel : pas d'auto-sync, pas de push local→Turso.
    Retourne un dict {outils, versions, recettes} des compteurs.
    """
    remote = TursoCatalogueDB()
    local = CatalogueDB()

    cur = remote.client.execute("SELECT * FROM catalogue_outils")
    outils = cur.fetchall()
    ocols = _cols(cur)

    out_count = ver_count = rec_count = 0

    for row in outils:
        d = dict(zip(ocols, row))
        local.conn.execute("""
            INSERT INTO catalogue_outils (ref, nom, description, tool_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ref) DO UPDATE SET
                nom=excluded.nom, description=excluded.description, tool_type=excluded.tool_type
        """, (d["ref"], d.get("nom"), d.get("description"), d.get("tool_type")))
        out_count += 1

        lo = local.conn.execute("SELECT outil_id FROM catalogue_outils WHERE ref=?", (d["ref"],)).fetchone()
        if not lo:
            continue
        lid = lo["outil_id"]

        cur2 = remote.client.execute(
            "SELECT * FROM catalogue_versions WHERE outil_id=?", (d["outil_id"],))
        versions = cur2.fetchall()
        vcols = _cols(cur2)

        for vr in versions:
            vd = dict(zip(vcols, vr))
            local.conn.execute("""
                INSERT INTO catalogue_versions (outil_id, nom_version, description)
                VALUES (?, ?, ?)
                ON CONFLICT(outil_id, nom_version) DO UPDATE SET
                    description=COALESCE(excluded.description, catalogue_versions.description)
            """, (lid, vd["nom_version"], vd.get("description")))
            ver_count += 1

            lv = local.conn.execute(
                "SELECT version_id FROM catalogue_versions WHERE outil_id=? AND nom_version=?",
                (lid, vd["nom_version"])).fetchone()
            if not lv:
                continue
            lvid = lv["version_id"]

            cur3 = remote.client.execute(
                "SELECT * FROM catalogue_recettes WHERE version_id=?", (vd["version_id"],))
            recettes = cur3.fetchall()
            rcols = _cols(cur3)

            for rr in recettes:
                rd = dict(zip(rcols, rr))
                local.conn.execute("""
                    INSERT INTO catalogue_recettes (version_id, os, arch, manager, package, content, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(version_id, os, arch, manager) DO UPDATE SET
                        package=COALESCE(excluded.package, catalogue_recettes.package),
                        content=COALESCE(excluded.content, catalogue_recettes.content),
                        confidence=excluded.confidence
                """, (lvid, rd["os"], rd["arch"], rd["manager"],
                      rd.get("package"), rd.get("content"), rd.get("confidence", 1.0)))
                rec_count += 1

        # Popularité
        cur4 = remote.client.execute(
            "SELECT * FROM outils_popularite WHERE outil_id=?", (d["outil_id"],))
        pop = cur4.fetchone()
        if pop:
            pcols = _cols(cur4)
            pd = dict(zip(pcols, pop))
            local.conn.execute("""
                INSERT INTO outils_popularite (outil_id, nb_install, nb_desinstall)
                VALUES (?, ?, ?)
                ON CONFLICT(outil_id) DO UPDATE SET
                    nb_install=excluded.nb_install, nb_desinstall=excluded.nb_desinstall,
                    updated_at=strftime('%s','now')
            """, (lid, pd.get("nb_install", 0), pd.get("nb_desinstall", 0)))

    local.conn.commit()
    local.close()
    return {"outils": out_count, "versions": ver_count, "recettes": rec_count}


# ──────────────────────────────────────────────
#  Main DB class
# ──────────────────────────────────────────────


class AgentDBMixin:
    """Agent OS repositories (importés séparément pour éviter les dépendances circulaires)."""

    def _init_agent_repos(self):
        from modules.sql.agent_repository import (
            AgentRepository, AgentMessageRepository,
            ModelProviderRepository, SessionRepository, WakeupCallRepository,
        )
        self.model_providers = ModelProviderRepository(self.conn)
        self.agents = AgentRepository(self.conn)
        self.sessions = SessionRepository(self.conn)
        self.agent_messages = AgentMessageRepository(self.conn)
        self.wakeup_calls = WakeupCallRepository(self.conn)


class OrchestrationDBMixin:
    """Orchestration repositories (queue, chatroom, todo, watchers)."""

    def _init_orchestration_repos(self):
        from modules.sql.orchestration_repository import (
            AgentQueueRepository, ChatroomRepository,
            SharedTaskRepository, WatcherRepository, ConnectionRepository,
        )
        self.queue = AgentQueueRepository(self.conn)
        self.chatroom = ChatroomRepository(self.conn)
        self.shared_tasks = SharedTaskRepository(self.conn)
        self.watchers = WatcherRepository(self.conn)
        self.connections = ConnectionRepository(self.conn)


class ModelWeaverDB(AgentDBMixin, OrchestrationDBMixin):
    """Point d'entrée unique pour la base locale.

    Crée automatiquement les tables si elles n'existent pas.

    Usage:
        db = ModelWeaverDB()
        for prov in db.providers.list_all():
            print(prov["name"])
        db.close()
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_local_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit (voir CatalogueDB) : évite les locks d'écriture persistants
        # entre les services partageant modelweaver.db.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()

        self.remote_catalogue = None

        # Appliquer les migrations SQL
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        if migrations_dir.exists():
            manager = MigrationManager(self.db_path)
            manager.apply_migrations(migrations_dir)

        self.providers = ProviderRepository(self.conn)
        self.models = ModelRepository(self.conn)
        self.keys = KeyRepository(self.conn)
        self.local_tools = LocalToolRepository(self.conn)
        self.system_state = SystemStateRepository(self.conn)
        self.llms = LocalLLMRepository(self.conn)
        self.commands = CommandRepository(self.conn)
        self._init_agent_repos()
        self._init_orchestration_repos()
        from modules.sql.agent_repository import ScheduledJobRepository
        self.scheduled_jobs = ScheduledJobRepository(self.conn)

    def _ensure_schema(self):
        """Crée les tables si elles n'existent pas encore.

        Applique tout le schema à chaque connexion (sûr grâce à IF NOT EXISTS).
        """
        schema = Path(__file__).resolve().parent / "modelweaver_schema.sql"
        if schema.exists():
            self.conn.executescript(schema.read_text())

        # ── capacite_log : journal LÉGER des observations de capacité ──
        # (table simple à deux entrées ok/fail ; la table d'expérience
        # model_endpoint_provider_capacite est mise à jour PAR BATCH via
        # flush_capacite_log).
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS capacite_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_id  INTEGER,
                model_id     INTEGER,
                capability   TEXT NOT NULL,
                ok           INTEGER NOT NULL DEFAULT 1,
                created_at   INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        try:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cap_log_model "
                "ON capacite_log(model_id)")
        except Exception:
            pass

        # Migration: ajouter key_display à api_keys
        try:
            self.conn.execute("ALTER TABLE api_keys ADD COLUMN key_display TEXT")
        except Exception:
            self.conn.rollback()        # Migration: ajouter locked à api_keys
        try:
            self.conn.execute("ALTER TABLE api_keys ADD COLUMN locked INTEGER DEFAULT 0")
        except Exception:
            self.conn.rollback()

        # Migration: colonnes de probe fine (point B/E) sur provider_models :
        #   deprecated : modèle périmé/disparu du listing provider (ne pas
        #                confondre avec unavailable transitoire)
        #   slow       : modèle lent mais joignable (timeout), à ne pas traiter
        #                comme bloqué
        for col in ("deprecated", "slow"):
            try:
                self.conn.execute(
                    f"ALTER TABLE provider_models ADD COLUMN {col} INTEGER DEFAULT 0")
            except Exception:
                self.conn.rollback()

        # Table d'état système
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                os TEXT,
                architecture TEXT,
                os_version TEXT,
                detected_managers TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # tool_usage : état d'install local par machine (télémétrie opt-in, phase 2/3).
        # Stocké dans l'inventory (modelweaver.db) car purement local.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                install_id TEXT,
                outil_ref TEXT,
                version_ref TEXT,
                recette_id INTEGER,
                etat TEXT CHECK(etat IN ('installed','uninstalled','upgraded')),
                ts INTEGER DEFAULT (strftime('%s','now'))
            )
        """)

        # ── Migration classes_outils (taxonomie métier) ──
        # Le schéma modelweaver_schema.sql crée déjà classes_outils (+seed)
        # et local_outils(classe_outil_id) pour les DB neuves. Pour les DB
        # locales legacy (local_outils sans classe_outil_id), on ajoute la
        # colonne et on backfill via le mapping par défaut.
        try:
            _ensure_classes_outils_table(self.conn)
            _add_column_if_missing(
                self.conn, "local_outils", "classe_outil_id",
                "INTEGER REFERENCES classes_outils(classe_id) ON DELETE SET NULL",
            )
            try:
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_local_outils_classe "
                    "ON local_outils(classe_outil_id)"
                )
            except Exception:
                pass
            # Backfill : tout outil local sans classe reçoit la classe par défaut
            # déduite de son ref (fallback 'other').
            for row in self.conn.execute(
                "SELECT local_outil_id, outil_ref FROM local_outils WHERE classe_outil_id IS NULL"
            ).fetchall():
                cid = resolve_classe_id(self.conn, _default_class_for_ref(row["outil_ref"]))
                if cid is not None:
                    self.conn.execute(
                        "UPDATE local_outils SET classe_outil_id=? WHERE local_outil_id=?",
                        (cid, row["local_outil_id"]))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration classes_outils (local) ignorée: {e}")
        # Commit final : ferme la transaction implicite du DDL ci-dessus
        # (sinon le lock d'écriture WAL reste tenu par le process toute sa vie).
        try:
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def scan_installed_tools(self) -> int:
        """Détecte les outils installés et met à jour local_outils/versions/installs."""
        import re, shutil, subprocess, sys, platform
        count = 0
        local_os = platform.system().lower()
        local_arch = platform.machine().lower()
        local_arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(local_arch, local_arch)

        binaries = {
            "ollama": ("ollama", "--version"),
            "opencode": ("opencode", "--version"),
            "python3": ("python3", "--version"),
            "git": ("git", "--version"),
            "curl": ("curl", "--version"),
        }
        for ref, (cmd, ver_flag) in binaries.items():
            path = shutil.which(cmd)
            if not path:
                continue
            version = None
            try:
                r = subprocess.run([cmd, ver_flag], capture_output=True, text=True, timeout=5)
                m = re.search(r"(\d+\.\d+(?:\.\d+)?)", r.stdout or "")
                version = m.group(1) if m else None
            except Exception:
                pass
            self.local_tools.save({
                "outil_ref": ref, "nom": ref, "tool_type": "binary",
                "nom_version": version or "unknown", "manager": "binary",
                "os": local_os, "arch": local_arch,
                "version_installee": version or "unknown",
                "install_path": path, "status": "installed",
            })
            count += 1

        pip_tools = {
            "litellm": "litellm", "open-webui": "open_webui", "gitingest": "gitingest",
            "keyring": "keyring", "requests": "requests", "psutil": "psutil",
            "cryptography": "cryptography",
        }
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                import json
                pip_packages = json.loads(r.stdout)
                pip_map = {p["name"].lower().replace("-", "_"): p["version"]
                           for p in pip_packages}
                for ref, pkg_name in pip_tools.items():
                    version = pip_map.get(pkg_name.lower())
                    if not version:
                        continue
                    self.local_tools.save({
                        "outil_ref": ref, "nom": ref, "tool_type": "python-module",
                        "nom_version": version, "manager": "pip",
                        "os": local_os, "arch": local_arch,
                        "version_installee": version,
                        "install_path": sys.executable, "status": "installed",
                    })
                    count += 1
        except Exception:
            pass
        self.commit()
        return count

    @contextmanager
    def transaction(self):
        """Gestionnaire de contexte pour les transactions."""
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


class CatalogueDB:
    """Point d'entrée pour la base catalogue (distant / synchro).

    Crée automatiquement les tables si elles n'existent pas.
    Usage:
        cat = CatalogueDB()
        cat.sync_from_url("http://localhost:8765/api")
        cat.close()
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_catalogue_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit : plusieurs services (daemon, agent-manager, llm-manager,
        # watcher) partagent catalogue.db. Sans autocommit, un write non
        # commité laisse une transaction implicite → lock d'écriture tenu →
        # "database is locked" au démarrage des autres services.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()

    def _ensure_schema(self):
        """Crée les tables si elles n'existent pas encore.
        Le script SQL inclut un seed idempotent (INSERT OR IGNORE) des
        providers. Si la BDD préexiste mais est vide (cas d'un catalogue
        initialisé sans seed), on rejoue le script pour peupler les providers."""
        schema = Path(__file__).resolve().parent / "catalogue_schema.sql"
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='catalogue_providers'"
        )
        if not cur.fetchone():
            if schema.exists():
                self.conn.executescript(schema.read_text())
        else:
            # ── capacite_log : journal léger des observations de capacité
            # (maj de model_endpoint_provider_capacite PAR BATCH via
            # flush_capacite_log) ──
            try:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS capacite_log (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        provider_id  INTEGER,
                        model_id     INTEGER,
                        capability   TEXT NOT NULL,
                        ok           INTEGER NOT NULL DEFAULT 1,
                        created_at   INTEGER DEFAULT (strftime('%s','now'))
                    )
                """)
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_cap_log_model "
                    "ON capacite_log(model_id)")
            except Exception:
                pass
            # Migration: ajoute provider_models si manquant
            try:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS provider_models (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        provider_id INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                        model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                        provider_model_name TEXT NOT NULL,
                        context_window_tokens INTEGER,
                        max_output_tokens INTEGER,
                        cost_per_input_token TEXT,
                        cost_per_output_token TEXT,
                        cost_per_thinking_token TEXT,
                        status TEXT DEFAULT 'active' CHECK(status IN ('active','deprecated','experimental')),
                        created_at INTEGER DEFAULT (strftime('%s','now')),
                        updated_at INTEGER DEFAULT (strftime('%s','now')),
                        UNIQUE(provider_id, model_id))
                """)
            except Exception:
                self.conn.rollback()
            # Migration: nouvelles tables catalogue outils/versions/recettes/popularité
            try:
                self.conn.executescript("""
                    CREATE TABLE IF NOT EXISTS catalogue_outils (
                        outil_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ref TEXT UNIQUE NOT NULL, nom TEXT NOT NULL,
                        fabricant TEXT, description TEXT,
                        tool_type TEXT CHECK(tool_type IN ('binary','python-module','archive','source','container')),
                        created_at INTEGER DEFAULT (strftime('%s','now')));
                    CREATE TABLE IF NOT EXISTS catalogue_versions (
                        version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        outil_id INTEGER NOT NULL REFERENCES catalogue_outils(outil_id) ON DELETE CASCADE,
                        nom_version TEXT NOT NULL, description TEXT,
                        created_at INTEGER DEFAULT (strftime('%s','now')),
                        UNIQUE(outil_id, nom_version));
                    CREATE TABLE IF NOT EXISTS catalogue_recettes (
                        recette_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        version_id INTEGER NOT NULL REFERENCES catalogue_versions(version_id) ON DELETE CASCADE,
                        os TEXT NOT NULL DEFAULT 'all', arch TEXT NOT NULL DEFAULT 'all',
                        manager TEXT, package TEXT, confidence REAL DEFAULT 1.0,
                        createur_id TEXT DEFAULT 'system',
                        install_count INTEGER DEFAULT 0, uninstall_count INTEGER DEFAULT 0,
                        content TEXT, enabled INTEGER DEFAULT 1,
                        created_at INTEGER DEFAULT (strftime('%s','now')),
                        updated_at INTEGER DEFAULT (strftime('%s','now')));
                    CREATE TABLE IF NOT EXISTS outils_popularite (
                        outil_id INTEGER PRIMARY KEY REFERENCES catalogue_outils(outil_id) ON DELETE CASCADE,
                        nb_install INTEGER DEFAULT 0, nb_desinstall INTEGER DEFAULT 0,
                        updated_at INTEGER DEFAULT (strftime('%s','now')));
                    CREATE INDEX IF NOT EXISTS idx_recettes_version ON catalogue_recettes(version_id);
                    CREATE INDEX IF NOT EXISTS idx_outils_ref ON catalogue_outils(ref);
                """)
            except Exception:
                self.conn.rollback()
            # Migration: ajouter tool_type aux DB existantes
            try:
                self.conn.execute("ALTER TABLE catalogue_outils ADD COLUMN tool_type TEXT")
            except Exception:
                self.conn.rollback()

        # ── Migration classes_outils (taxonomie métier) ──
        # Couvre les deux branches ci-dessus :
        #  - DB vierge (schéma chargé via catalogue_schema.sql) : la table
        #    classes_outils + la colonne classe_outil_id sont déjà créées ;
        #  - DB legacy (branche else) : il faut les créer/ajouter.
        #  - DB legacy partielle où catalogue_outils existait DÉJÀ au chargement
        #    du .sql (IF NOT EXISTS ignoré) : on ajoute la colonne ici.
        try:
            _ensure_classes_outils_table(self.conn)
            _add_column_if_missing(
                self.conn, "catalogue_outils", "classe_outil_id",
                "INTEGER REFERENCES classes_outils(classe_id) ON DELETE SET NULL",
            )
            try:
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_outils_classe "
                    "ON catalogue_outils(classe_outil_id)"
                )
            except Exception:
                self.conn.rollback()
            # Backfill : tout outil sans classe_outil_id reçoit la classe par défaut
            # déduite de son ref (fallback 'other').
            for row in self.conn.execute(
                "SELECT outil_id, ref FROM catalogue_outils WHERE classe_outil_id IS NULL"
            ).fetchall():
                classe_ref = _default_class_for_ref(row["ref"])
                cid = resolve_classe_id(self.conn, classe_ref)
                if cid is not None:
                    self.conn.execute(
                        "UPDATE catalogue_outils SET classe_outil_id=? WHERE outil_id=?",
                        (cid, row["outil_id"]))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration classes_outils ignorée: {e}")

        # ── Migration catalogue_aliases (réconciliation noms externes) ──
        try:
            # Si l'ancienne structure (entity_type sans target) existe, on
            # la recrée proprement (dev) pour adopter source/target/scope.
            cur = self.conn.execute("PRAGMA table_info(catalogue_aliases)").fetchall()
            cols = {row[1] for row in cur}
            if cur and "target" not in cols:
                self.conn.execute("DROP TABLE IF EXISTS catalogue_aliases")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS catalogue_aliases (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    source        TEXT NOT NULL,
                    target        TEXT NOT NULL,
                    scope         TEXT NOT NULL CHECK(scope IN ('provider','model','provider-model')),
                    alias         TEXT NOT NULL,
                    canonical_ref TEXT NOT NULL,
                    priority      INTEGER DEFAULT 0,
                    created_at    INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(source, target, scope, alias)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_target_scope "
                "ON catalogue_aliases(target, scope)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_canonical "
                "ON catalogue_aliases(canonical_ref)")
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration catalogue_aliases ignorée: {e}")

        # ── Seed si tables vides ──
        # Couvre le cas d'une BDD pré-existante (tables créées) mais non
        # peuplée : le seed INSERT OR IGNORE du .sql est idempotent.
        try:
            pc = self.conn.execute("SELECT COUNT(*) FROM catalogue_providers").fetchone()[0]
            ec = 0
            try:
                ec = self.conn.execute("SELECT COUNT(*) FROM provider_endpoints").fetchone()[0]
            except Exception:
                ec = 0
            if (pc == 0 or ec == 0) and schema.exists():
                self.conn.executescript(schema.read_text())
                self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Seed ignoré: {e}")

        # ── Migration colonnes provider_endpoints ──
        try:
            _add_column_if_missing(self.conn, "provider_endpoints", "local_latency", "REAL")
            _add_column_if_missing(self.conn, "provider_endpoints", "global_quality", "REAL")
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration provider_endpoints ignorée: {e}")

        # ── Migration model_key (identifiant canonique par MODÈLE) ──
        # Permet d'agréger les scores de benchmark par modèle (pas par
        # provider_model) et de comparer les variantes entre elles.
        try:
            _add_column_if_missing(self.conn, "catalogue_models", "model_key", "TEXT")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cat_models_model_key "
                "ON catalogue_models(model_key)")
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration model_key ignorée: {e}")

        # ── Migration official (source certaine) + nom obligatoire ──
        # official = 1 si le modèle est « certain » (nom confirmé par une source
        # fiable, ex. fiche éditeur / Hugging Face), 0 s'il est déduit par
        # l'expérience (un provider l'expose sans confirmation officielle).
        # Backfill : un modèle sans name prend le premier nom trouvé (ref / alias).
        try:
            _add_column_if_missing(self.conn, "catalogue_models", "official",
                                   "INTEGER DEFAULT 0")
            # Nom minimum obligatoire à l'insertion (contrainte déclenchée sur
            # les lignes existantes vides → backfill AVANT d'ajouter le CHECK).
            empties = self.conn.execute(
                "SELECT id, ref FROM catalogue_models "
                "WHERE name IS NULL OR TRIM(name) = ''").fetchall()
            for row in empties:
                fallback = row["ref"] or f"model-{row['id']}"
                self.conn.execute(
                    "UPDATE catalogue_models SET name = ? WHERE id = ?",
                    (fallback, row["id"]))
            self.conn.commit()
            # Official par défaut : 1 si le ref ressemble à un nom canonique
            # (pas un placeholder générique). Affiné ensuite par le scraper.
            self.conn.execute("""
                UPDATE catalogue_models SET official = 1
                WHERE official = 0 AND name IS NOT NULL AND TRIM(name) != ''
                  AND ref != '' AND ref NOT LIKE 'model-%'
            """)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration official ignorée: {e}")

        # ── Migration context_window_effective + context_audit_log ──
        try:
            _add_column_if_missing(self.conn, "provider_models", "context_window_effective", "INTEGER")
            _add_column_if_missing(self.conn, "provider_models", "available", "INTEGER DEFAULT 1")
            _add_column_if_missing(self.conn, "provider_models", "free_tier", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "provider_models", "cost_per_thinking_token", "TEXT")
            # Repos après échec runtime d'appel LLM (rate-limit / erreur) :
            #   unavailable      -> le dernier appel a échoué (booléen)
            #   noretryuntil     -> deadline (seconde) avant laquelle on ne retente pas
            #   notrytime        -> durée d'interdiction posée la dernière fois (×2)
            _add_column_if_missing(self.conn, "provider_models", "unavailable", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "provider_models", "noretryuntil", "REAL DEFAULT 0")
            _add_column_if_missing(self.conn, "provider_models", "notrytime", "REAL DEFAULT 0")
            # Agentic = le modèle sait utiliser les tools (tool calling fiable).
            # Rempli par le probe (bridge.probe) : 1 si le modèle appelle un
            # outil de façon cohérente, 0 sinon / inconnu.
            _add_column_if_missing(self.conn, "provider_models", "agentic", "INTEGER DEFAULT 0")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS context_audit_log (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_ref          TEXT NOT NULL,
                    model_ref             TEXT NOT NULL,
                    tokens_sent           INTEGER NOT NULL,
                    detected_context_limit INTEGER,
                    context_window_effective INTEGER,
                    created_at            INTEGER DEFAULT (strftime('%s', 'now'))
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_provider_model "
                "ON context_audit_log(provider_ref, model_ref)")
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration context_audit_log ignorée: {e}")

        # ── Migration : journal des appels LLM réels (métriques runtime) ──
        # Chaque appel bridge.log_call_* écrit une ligne ici. Utilisé par le
        # scoring d'allocation (taux de succès, latence, tokens) via une
        # fenêtre glissante. Référencé par ID (provider_id/model_id), pas par
        # nom. Fenêtre bornée : on purge au-delà de 10k lignes.
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS model_call_log (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id      INTEGER NOT NULL,
                    model_id         INTEGER NOT NULL,
                    provider_model_id INTEGER,
                    agent_id         TEXT,
                    success          INTEGER NOT NULL DEFAULT 1,
                    tokens_in        INTEGER DEFAULT 0,
                    tokens_out       INTEGER DEFAULT 0,
                    tokens_thinking  INTEGER DEFAULT 0,
                    latency_ms       REAL DEFAULT 0,
                    error_code       TEXT,
                    error_msg        TEXT,
                    call_type        TEXT DEFAULT 'chat',
                    created_at       INTEGER DEFAULT (strftime('%s', 'now'))
                )
            """)
            _add_column_if_missing(self.conn, "model_call_log", "agent_id", "TEXT")
            _add_column_if_missing(self.conn, "model_call_log", "error_msg", "TEXT")
            _add_column_if_missing(self.conn, "model_call_log", "call_type", "TEXT DEFAULT 'chat'")
            # Source de l'appel LLM : agent:N / bridge / service:model_sync / probe…
            # Permet de reconstruire les SESSIONS PAR APPELLANT (llm_caller_sessions)
            # sans confondre avec les séquences globales par modèle.
            _add_column_if_missing(self.conn, "model_call_log", "caller_id", "TEXT")
            # Méta de l'appel agentic : {agentic_mode, success_tool,
            # success_translation} renseignés par le FSM à chaque llm_call.
            _add_column_if_missing(self.conn, "model_call_log", "meta_json", "TEXT")
            # Idée 18 : adresse_id (provider×endpoint×modèle) — les sessions
            # succès/fail (usage_batcher) sont tracées par adresse.
            _add_column_if_missing(self.conn, "model_call_log", "adresse_id", "INTEGER")
            # Lien structurel séquence→tâche : colonnes DÉDIÉES (pas de JSON).
            # Chaque appel LLM sait de quelle (sub)task il vient — injectées par
            # le FSM (ask_new_task/sub_task_get) → le suivi budgétaire par tâche
            # (task_budget_tracking) reconstruit le budget UTILISÉ par requête
            # indexée, sans LIKE sur meta_json.
            _add_column_if_missing(self.conn, "model_call_log", "task_id", "INTEGER")
            _add_column_if_missing(self.conn, "model_call_log", "sub_task_id", "INTEGER")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_log_provider_model "
                "ON model_call_log(provider_id, model_id, id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_log_agent "
                "ON model_call_log(agent_id, id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_log_created "
                "ON model_call_log(created_at, id)")
            # ── Archive des lignes purgées (sur disque, pas en RAM) ──
            # Quand la table principale dépasse la fenêtre (10k), les lignes
            # les plus anciennes sont déplacées ici au lieu d'être supprimées.
            # Permet les analyses de patterns (limites réelles, usages, quotas)
            # sans alourdir le scoring (qui lit la table principale).
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS model_call_log_archive (
                    id               INTEGER PRIMARY KEY,
                    provider_id      INTEGER NOT NULL,
                    model_id         INTEGER NOT NULL,
                    provider_model_id INTEGER,
                    agent_id         TEXT,
                    success          INTEGER NOT NULL DEFAULT 1,
                    tokens_in        INTEGER DEFAULT 0,
                    tokens_out       INTEGER DEFAULT 0,
                    tokens_thinking  INTEGER DEFAULT 0,
                    latency_ms       REAL DEFAULT 0,
                    error_code       TEXT,
                    error_msg        TEXT,
                    call_type        TEXT DEFAULT 'chat',
                    caller_id        TEXT,
                    created_at       INTEGER DEFAULT (strftime('%s', 'now')),
                    archived_at      INTEGER DEFAULT (strftime('%s', 'now'))
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_created "
                "ON model_call_log_archive(created_at, id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_model "
                "ON model_call_log_archive(provider_id, model_id, id)")
            _add_column_if_missing(self.conn, "model_call_log_archive",
                                   "caller_id", "TEXT")
            # Lien structurel séquence→tâche propagé à l'archive (le rebuild
            # des séquences lit archive + détail — il faut y retrouver les
            # tâches pour le suivi budgétaire rétroactif).
            _add_column_if_missing(self.conn, "model_call_log_archive",
                                   "task_id", "INTEGER")
            _add_column_if_missing(self.conn, "model_call_log_archive",
                                   "sub_task_id", "INTEGER")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_caller "
                "ON model_call_log_archive(caller_id, id)")
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration model_call_log ignorée: {e}")

        # ── Migration : nouvelles tables d'acces modeles (V0.7.0.4+) ──
        # V0.9 : renommée key_endpoint_models → provider_models_mapping
        # (human-choice #28). Migration de données si l'ancienne table existe.
        try:
            has_old = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='key_endpoint_models'").fetchone()
            has_new = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='provider_models_mapping'").fetchone()
            if has_old and not has_new:
                self.conn.execute("""
                    ALTER TABLE key_endpoint_models
                    RENAME TO provider_models_mapping
                """)
            elif has_old and has_new:
                # Les DEUX existent (migration partielle antérieure) : on
                # abandonne l'ancienne pour éviter le conflit de nom. Les
                # données utiles sont déjà dans provider_models_mapping.
                try:
                    self.conn.execute("DROP TABLE key_endpoint_models")
                except Exception:
                    pass
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS provider_models_mapping (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                    endpoint_id INTEGER NOT NULL REFERENCES provider_endpoints(endpoint_id) ON DELETE CASCADE,
                    key_ref TEXT NOT NULL,
                    model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    provider_model_name TEXT NOT NULL,
                    declared INTEGER DEFAULT 0,
                    available INTEGER DEFAULT 0,
                    last_checked_at INTEGER,
                    last_error TEXT,
                    created_at INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(endpoint_id, key_ref, model_id)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS model_efficacy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    use_case TEXT NOT NULL,
                    score_quality REAL DEFAULT 0, score_speed REAL DEFAULT 0,
                    score_cost REAL DEFAULT 0, score_reliability REAL DEFAULT 0,
                    samples INTEGER DEFAULT 0,
                    updated_at INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(model_id, use_case)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS model_provider_scoring (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id           INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                    model_id              INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    endpoint_id           INTEGER REFERENCES provider_endpoints(endpoint_id) ON DELETE SET NULL,
                    latency_avg_ms        REAL DEFAULT 0,
                    latency_p95_ms        REAL DEFAULT 0,
                    latency_stddev_ms     REAL DEFAULT 0,
                    latency_samples       INTEGER DEFAULT 0,
                    quality_score         REAL DEFAULT 0,
                    score_chat            REAL DEFAULT 0,
                    score_coding          REAL DEFAULT 0,
                    score_reasoning       REAL DEFAULT 0,
                    score_knowledge       REAL DEFAULT 0,
                    score_agentic         REAL DEFAULT 0,
                    cost_per_1k_input     REAL DEFAULT 0,
                    cost_per_1k_output    REAL DEFAULT 0,
                    global_score          REAL DEFAULT 0,
                    is_synthetic          INTEGER DEFAULT 0,
                    benchmark_ref         TEXT DEFAULT '',
                    last_scored_at        INTEGER DEFAULT (strftime('%s','now')),
                    confidence            REAL DEFAULT 0,
                    created_at            INTEGER DEFAULT (strftime('%s','now')),
                    updated_at            INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(provider_id, model_id)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS alias_model (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id      INTEGER REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    source_name   TEXT NOT NULL,
                    source        TEXT NOT NULL,
                    source_type   TEXT NOT NULL CHECK(source_type IN ('provider','benchmark')),
                    confidence    TEXT DEFAULT 'auto',
                    status        TEXT NOT NULL DEFAULT 'linked'
                                  CHECK(status IN ('linked','unresolved','ambiguous')),
                    updated_at    INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(source, source_type, source_name)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alias_model_mid ON alias_model(model_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alias_model_src ON alias_model(source, source_type)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alias_model_name ON alias_model(source_name)")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                    unit TEXT, scope TEXT
                )
            """)
            self.conn.execute("""
                INSERT OR IGNORE INTO budget_tags (code, label, unit, scope) VALUES
                    ('req_per_min','Requetes / minute','requests','requests'),
                    ('req_per_day','Requetes / jour','requests','requests'),
                    ('tok_per_min','Tokens / minute','tokens','tokens'),
                    ('tok_per_hour','Tokens / heure','tokens','tokens'),
                    ('tok_per_day','Tokens / jour','tokens','tokens'),
                    ('cost_per_day','Cout / jour (USD)','usd','cost'),
                    ('cost_per_month','Cout / mois (USD)','usd','cost')
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL CHECK(target_type IN ('provider','model','endpoint','key')),
                    target_ref TEXT NOT NULL,
                    tag_id INTEGER NOT NULL REFERENCES budget_tags(id),
                    limit_value REAL NOT NULL,
                    window TEXT NOT NULL DEFAULT 'day' CHECK(window IN ('minute','hour','day','month')),
                    cost_per_unit REAL,
                    created_at INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
            # ── State des probes modèles (sync périodique des clés) ──
            # Timeout croissant par (endpoint, key, model) : quand un modèle
            # échoue au probe, son prochain essai est retardé de plus en plus
            # (backoff) pour ne pas marteler une API qui répond en erreur.
            # Après trop d'échecs consécutifs, le modèle est marqué defunct.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS model_probe_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                    endpoint_id INTEGER NOT NULL REFERENCES provider_endpoints(endpoint_id) ON DELETE CASCADE,
                    key_ref TEXT NOT NULL,
                    model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    consecutive_failures INTEGER DEFAULT 0,
                    backoff_s INTEGER DEFAULT 0,
                    next_probe_at INTEGER DEFAULT 0,
                    last_status TEXT,
                    last_error TEXT,
                    last_probed_at INTEGER,
                    defunct INTEGER DEFAULT 0,
                    created_at INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(endpoint_id, key_ref, model_id)
                )
            """)

            # ── Capacités PAR (modèle, endpoint, provider) ──
            # Un même modèle peut avoir des capacités différentes selon
            # l'endpoint/provider qui le sert (ex. tool-calling dispo sur
            # google direct mais bridé sur un proxy). Chaque capacité porte un
            # SCORE DE CONFIANCE 0-1 : il monte quand la capacité est
            # réellement utilisée avec succès, baisse quand elle échoue.
            #   - bool (agentic, vision, supports_chat, streaming/sse,
            #           function_calling, reasoning) : confidence + compteurs
            #   - range (context_window, max_input, max_output) : min/max
            #     observés + nb observations.
            # La capacité OFFICIELLE du modèle (catalogue_models/model_capabilities)
            # est la BORNE HAUTE ; celle-ci est l'EXPÉRIENCE (≤ borne).
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS model_endpoint_provider_capacite (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id      INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    endpoint_id   INTEGER NOT NULL REFERENCES provider_endpoints(endpoint_id) ON DELETE CASCADE,
                    provider_id   INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                    capability    TEXT NOT NULL,
                    cap_type      TEXT NOT NULL CHECK(cap_type IN ('bool','range')),
                    confidence    REAL DEFAULT 0.5,
                    yes_count     INTEGER DEFAULT 0,
                    no_count      INTEGER DEFAULT 0,
                    min_value     INTEGER,
                    max_value     INTEGER,
                    observations  INTEGER DEFAULT 0,
                    source        TEXT DEFAULT 'experience'
                                  CHECK(source IN ('api','experience','manual')),
                    last_observed_at INTEGER,
                    updated_at    INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(model_id, endpoint_id, provider_id, capability)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mepc_model "
                "ON model_endpoint_provider_capacite(model_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mepc_endpoint "
                "ON model_endpoint_provider_capacite(endpoint_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mepc_cap "
                "ON model_endpoint_provider_capacite(capability)")
            # ── capacite_log : journal LÉGER des observations de capacité ──
            # Table simple à deux entrées (ok/fail). La table d'expérience
            # model_endpoint_provider_capacite est mise à jour PAR BATCH depuis
            # ici (flush_capacite_log) au lieu d'écrire à chaque appel LLM.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS capacite_log (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id  INTEGER,
                    model_id     INTEGER,
                    capability   TEXT NOT NULL,
                    ok           INTEGER NOT NULL DEFAULT 1,
                    created_at   INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cap_log_model "
                "ON capacite_log(model_id)")
        except Exception as e:
            import sys as _sys
            self.conn.rollback()
            print(f"⚠️  Migration tables d'acces ignoree: {e}", file=_sys.stderr)

        # ── Migration model_capabilities → model_id + official ──
        # Les capacités OFFICIELLES du modèle (bornes hautes, source certaine).
        # On remplace la clé model_ref par model_id (l'identifiant canonique)
        # et on ajoute `official` : 1 si la source est certaine (fiche éditeur /
        # API officielle), 0 si déduite par l'expérience ou un provider tiers.
        # NB : la table est créée historiquement par catalogue_sync/remote avec
        # UNIQUE(model_ref) ; on la RE-CRÉE avec UNIQUE(model_id) pour que
        # ON CONFLICT(model_id) fonctionne (un index unique ne suffit pas).
        try:
            has_old = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='model_capabilities'").fetchone()
            has_new = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='model_capabilities_v2'").fetchone()
            if has_old and not has_new:
                # Renommer l'ancienne → recréer avec la bonne contrainte.
                try:
                    self.conn.execute(
                        "ALTER TABLE model_capabilities RENAME TO model_capabilities_v2")
                except Exception:
                    pass
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS model_capabilities (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id        INTEGER UNIQUE
                                    REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    model_ref       TEXT,
                    supports_chat           INTEGER DEFAULT 0,
                    supports_function_calling INTEGER DEFAULT 0,
                    supports_vision         INTEGER DEFAULT 0,
                    supports_embedding      INTEGER DEFAULT 0,
                    supports_streaming      INTEGER DEFAULT 0,
                    supports_tools          INTEGER DEFAULT 0,
                    max_context_tokens      INTEGER,
                    max_output_tokens       INTEGER,
                    pricing_input_per_1k    REAL,
                    pricing_output_per_1k   REAL,
                    source                  TEXT DEFAULT 'unknown',
                    official                INTEGER DEFAULT 0,
                    last_updated_at         INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_mc_model_ref "
                "ON model_capabilities(model_ref)")
            # Table pré-existante : ALTER idempotent au cas où.
            _add_column_if_missing(self.conn, "model_capabilities", "official",
                                   "INTEGER DEFAULT 0")
            # Migration des données depuis l'ancienne table (si renommée).
            has_v2 = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='model_capabilities_v2'").fetchone()
            if has_v2:
                done = self.conn.execute(
                    "SELECT COUNT(*) FROM model_capabilities").fetchone()[0]
                if done == 0:
                    # Copier + résoudre model_id via ref/model_key.
                    self.conn.execute("""
                        INSERT OR IGNORE INTO model_capabilities
                            (model_id, model_ref, supports_chat,
                             supports_function_calling, supports_vision,
                             supports_embedding, supports_streaming,
                             supports_tools, max_context_tokens,
                             max_output_tokens, pricing_input_per_1k,
                             pricing_output_per_1k, source, official,
                             last_updated_at)
                        SELECT NULL, mc.model_ref, mc.supports_chat,
                               mc.supports_function_calling, mc.supports_vision,
                               mc.supports_embedding, mc.supports_streaming,
                               mc.supports_tools, mc.max_context_tokens,
                               mc.max_output_tokens, mc.pricing_input_per_1k,
                               mc.pricing_output_per_1k, mc.source,
                               CASE WHEN mc.source IN ('api','knowledge','remote','official')
                                    THEN 1 ELSE 0 END, mc.last_updated_at
                        FROM model_capabilities_v2 mc
                    """)
                    # Résoudre model_id par ref exacte puis model_key.
                    for row in self.conn.execute(
                            "SELECT id, model_ref FROM model_capabilities "
                            "WHERE model_id IS NULL AND model_ref IS NOT NULL"):
                        mid = self.conn.execute(
                            "SELECT id FROM catalogue_models WHERE ref = ? "
                            "OR model_key = ? LIMIT 1",
                            (row["model_ref"], row["model_ref"])).fetchone()
                        if mid:
                            self.conn.execute(
                                "UPDATE model_capabilities SET model_id = ? "
                                "WHERE id = ?", (mid["id"], row["id"]))
                self.conn.commit()
                try:
                    self.conn.execute("DROP TABLE model_capabilities_v2")
                    self.conn.commit()
                except Exception:
                    pass
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration model_capabilities ignorée: {e}")

        # ── Migration adresse_runtime : ADRESSE COMPLÈTE par (adresse, clé) ──
        # Construite de base depuis provider_model_address (l'adresse sans clé,
        # déjà remplie) — fluide au reboot. api_key_tag dénormalisé (free/plus/
        # premium). La clé en clair N'EST PAS ici (résolue en RAM au runtime).
        try:
            _add_column_if_missing(self.conn, "provider_model_address",
                                   "api_key_tag", "TEXT DEFAULT ''")
            # Table créée ICI pour les BDD pré-existantes (l'executescript du
            # schéma ne se rejoue que si le catalogue est vierge).
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS adresse_runtime (
                    adresse_runtime_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    adresse_id          INTEGER NOT NULL REFERENCES provider_model_address(adresse_id),
                    api_key_id          INTEGER NOT NULL,
                    api_key_tag         TEXT DEFAULT '',
                    provider_id         INTEGER,
                    provider_ref        TEXT DEFAULT '',
                    endpoint_id         INTEGER,
                    endpoint_url        TEXT DEFAULT '',
                    model_id            INTEGER,
                    model_key           TEXT DEFAULT '',
                    provider_model_id   INTEGER,
                    provider_model_name TEXT DEFAULT '',
                    api_type            TEXT DEFAULT '',
                    available           INTEGER DEFAULT 1,
                    error_since         INTEGER,
                    last_error_at       INTEGER,
                    backoff_until       INTEGER,
                    first_use           INTEGER,
                    last_use            INTEGER,
                    first_respond       INTEGER,
                    last_respond        INTEGER,
                    created_at          TEXT DEFAULT (datetime('now')),
                    UNIQUE(adresse_id, api_key_id)
                )
            """)
            # Bornes d'usage temporelles de l'adresse (Idée 18/P).
            for _col, _dflt in (("first_use", "INTEGER"),
                                ("last_use", "INTEGER"),
                                ("first_respond", "INTEGER"),
                                ("last_respond", "INTEGER")):
                _add_column_if_missing(self.conn, "adresse_runtime", _col, _dflt)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ar_adresse ON adresse_runtime(adresse_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ar_key ON adresse_runtime(api_key_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ar_tag ON adresse_runtime(api_key_tag)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ar_model ON adresse_runtime(model_key)")
            # Remplir depuis l'adresse (idempotent). api_key_id=0 pour l'instant
            # (la vraie clé est résolue au runtime depuis modelweaver.db — le
            # catalogue ne référence pas api_keys).
            self.conn.execute("""
                INSERT OR IGNORE INTO adresse_runtime
                    (adresse_id, api_key_id, api_key_tag,
                     provider_id, provider_ref, endpoint_id, endpoint_url,
                     model_id, model_key, provider_model_id,
                     provider_model_name, api_type)
                SELECT a.adresse_id, 0, a.api_key_tag,
                       a.provider_id, a.provider_ref, a.endpoint_id,
                       a.endpoint_url, a.model_id, a.model_key,
                       a.provider_model_id, a.provider_model_name,
                       COALESCE(pe.api_type, 'openai')
                FROM provider_model_address a
                LEFT JOIN provider_endpoints pe
                       ON pe.endpoint_id = a.endpoint_id
                WHERE a.available = 1
            """)
            self.conn.commit()
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            print(f"⚠️  Migration adresse_runtime ignorée: {e}")

        # ── Migration scores par NIVEAU (Idée 18) : init 1.0 partout ──
        # llm_domaine_score + llm_task_type_score : une ligne par (modèle,
        # domaine) / (modèle, type) avec 5 colonnes de niveau à 1.0 (neutre).
        # Le grain = catalogue_models (le modèle canonique). La mise à jour
        # par l'expérience viendra plus tard.
        try:
            # Tables de référence (domaines + types) — créées ICI pour les BDD
            # pré-existantes (l'executescript du schéma ne se rejoue que si
            # vierge).
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS scoring_domaines (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS scoring_task_types (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL
                )
            """)
            self.conn.execute("INSERT OR IGNORE INTO scoring_domaines (code, label) VALUES "
                              "('coding','Code'),('text_generation','Texte'),('math','Maths'),"
                              "('data','Données'),('reasoning','Raisonnement'),"
                              "('research','Recherche'),('admin','Admin')")
            self.conn.execute("INSERT OR IGNORE INTO scoring_task_types (code, label) VALUES "
                              "('planning','Découpe'),('coding','Code'),('reviewing','Relecture'),"
                              "('testing','Tests'),('merging','Fusion'),('respond','Réponse'),"
                              "('exploration','Exploration')")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_domaine_score (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id     INTEGER NOT NULL REFERENCES catalogue_models(id),
                    domaine_id   INTEGER NOT NULL REFERENCES scoring_domaines(id),
                    debutant     REAL DEFAULT 1.0,
                    junior       REAL DEFAULT 1.0,
                    intermediaire REAL DEFAULT 1.0,
                    senior       REAL DEFAULT 1.0,
                    expert       REAL DEFAULT 1.0,
                    samples      INTEGER DEFAULT 0,
                    updated_at   INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(model_id, domaine_id)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_task_type_score (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id     INTEGER NOT NULL REFERENCES catalogue_models(id),
                    task_type_id INTEGER NOT NULL REFERENCES scoring_task_types(id),
                    debutant     REAL DEFAULT 1.0,
                    junior       REAL DEFAULT 1.0,
                    intermediaire REAL DEFAULT 1.0,
                    senior       REAL DEFAULT 1.0,
                    expert       REAL DEFAULT 1.0,
                    samples      INTEGER DEFAULT 0,
                    updated_at   INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(model_id, task_type_id)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lds_model ON llm_domaine_score(model_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_lts_model ON llm_task_type_score(model_id)")
            # Init : une ligne par (modèle × domaine) et (modèle × type), à 1.0.
            # Seulement si vide (idempotent) — on ne recrée pas après l'expérience.
            _lds = self.conn.execute(
                "SELECT COUNT(*) c FROM llm_domaine_score").fetchone()
            if _lds and _lds["c"] == 0:
                self.conn.execute("""
                    INSERT OR IGNORE INTO llm_domaine_score (model_id, domaine_id)
                    SELECT DISTINCT cm.id, sd.id
                    FROM catalogue_models cm
                    CROSS JOIN scoring_domaines sd
                    WHERE cm.model_key != ''
                """)
                self.conn.execute("""
                    INSERT OR IGNORE INTO llm_task_type_score (model_id, task_type_id)
                    SELECT DISTINCT cm.id, st.id
                    FROM catalogue_models cm
                    CROSS JOIN scoring_task_types st
                    WHERE cm.model_key != ''
                """)
            self.conn.commit()
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            print(f"⚠️  Migration scores par niveau ignorée: {e}")

        # ── Migration budgets par tag de clé + manuels + finaux (Idée 18) ──
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_generique_key_tag (
                    budget_generique_key_tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    api_key_tag    TEXT NOT NULL DEFAULT '',
                    tag_id         INTEGER NOT NULL REFERENCES budget_tags(id),
                    quota          REAL NOT NULL,
                    spent          REAL DEFAULT 0,
                    souplesse      TEXT DEFAULT 'strict' CHECK(souplesse IN ('strict','souple','informatif')),
                    souplesse_taux REAL DEFAULT 0,
                    interval_reset TEXT NOT NULL DEFAULT 'day' CHECK(interval_reset IN ('minute','hour','day','month')),
                    next_reset     INTEGER,
                    session_start  TEXT DEFAULT '',
                    session_close  TEXT DEFAULT '',
                    rolling_hours  INTEGER DEFAULT 0,
                    error_rate_limite INTEGER DEFAULT 0,
                    created_at     INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(api_key_tag, tag_id, interval_reset)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_user_key_id (
                    budget_user_key_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_ref       TEXT NOT NULL,
                    tag_id         INTEGER NOT NULL REFERENCES budget_tags(id),
                    quota          REAL NOT NULL,
                    spent          REAL DEFAULT 0,
                    souplesse      TEXT DEFAULT 'strict',
                    souplesse_taux REAL DEFAULT 0,
                    interval_reset TEXT NOT NULL DEFAULT 'day',
                    next_reset     INTEGER,
                    session_start  TEXT DEFAULT '',
                    session_close  TEXT DEFAULT '',
                    rolling_hours  INTEGER DEFAULT 0,
                    created_at     INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(user_ref, tag_id, interval_reset)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_final (
                    budget_final_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    adresse_runtime_id INTEGER NOT NULL REFERENCES adresse_runtime(adresse_runtime_id),
                    budget_generique_key_tag_id INTEGER REFERENCES budget_generique_key_tag(budget_generique_key_tag_id),
                    budget_user_key_id INTEGER REFERENCES budget_user_key_id(budget_user_key_id),
                    tag_id         INTEGER NOT NULL REFERENCES budget_tags(id),
                    quota_effectif REAL NOT NULL,
                    spent          REAL DEFAULT 0,
                    souplesse      TEXT DEFAULT 'strict',
                    souplesse_taux REAL DEFAULT 0,
                    interval_reset TEXT NOT NULL DEFAULT 'day',
                    next_reset     INTEGER,
                    error_rate_limite INTEGER DEFAULT 0,
                    created_at     INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(adresse_runtime_id, tag_id, interval_reset)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bf_adresse ON budget_final(adresse_runtime_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bg_tag ON budget_generique_key_tag(api_key_tag)")
            self.conn.commit()
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            print(f"⚠️  Migration budgets par tag ignorée: {e}")

        # ── Migration coûts (Idée 18) : cost_key_tag + cost_final + tags ──
        try:
            # Compléter budget_tags avec les types multi-monnaies.
            self.conn.execute("INSERT OR IGNORE INTO budget_tags (code, label, unit, scope) VALUES "
                              "('request','Requete','request','request'),"
                              "('money','Argent (USD)','usd','money'),"
                              "('time','Temps (secondes)','seconds','time'),"
                              "('thinking_power','Puissance de pensée','think','thinking_power')")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS cost_key_tag (
                    cost_key_tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    adresse_key_tag_id INTEGER NOT NULL REFERENCES provider_model_address(adresse_id),
                    budget_generique_key_tag_id INTEGER REFERENCES budget_generique_key_tag(budget_generique_key_tag_id),
                    unit_in   TEXT NOT NULL,
                    unit_out  TEXT NOT NULL,
                    ratio     REAL NOT NULL DEFAULT 1.0,
                    created_at INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(adresse_key_tag_id, budget_generique_key_tag_id, unit_in, unit_out)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS cost_final (
                    cost_final_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    adresse_runtime_id INTEGER NOT NULL REFERENCES adresse_runtime(adresse_runtime_id),
                    budget_final_id INTEGER REFERENCES budget_final(budget_final_id),
                    unit_in   TEXT NOT NULL,
                    unit_out  TEXT NOT NULL,
                    ratio     REAL NOT NULL DEFAULT 1.0,
                    created_at INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(adresse_runtime_id, budget_final_id, unit_in, unit_out)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ckt_adresse ON cost_key_tag(adresse_key_tag_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cf_adresse ON cost_final(adresse_runtime_id)")
            self.conn.commit()
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            print(f"⚠️  Migration coûts ignorée: {e}")

        # ── Migration états d'erreur (Idée 18) : adress + budget_error_state ──
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS adress_error_state (
                    adresse_runtime_id INTEGER PRIMARY KEY REFERENCES adresse_runtime(adresse_runtime_id),
                    error_since    INTEGER,
                    last_error_at  INTEGER,
                    n_fail         INTEGER DEFAULT 0,
                    backoff_until  INTEGER,
                    updated_at     INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_error_state (
                    budget_id      INTEGER PRIMARY KEY,
                    adresse_count  INTEGER DEFAULT 0,
                    adresse_error  INTEGER DEFAULT 0,
                    error_since    INTEGER,
                    last_error_at  INTEGER,
                    backoff_until  INTEGER,
                    updated_at     INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_bes_budget ON budget_error_state(budget_id)")
            self.conn.commit()
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            print(f"⚠️  Migration états d'erreur ignorée: {e}")

        # ── Migration allocation agent→budget (Idée 18) : colonne alloue sur
        # budget_final (partie du quota ENGAGÉE sur les allocations — pas une
        # consommation) + table agent_budget_allocation (donnée à un agent).
        # Deux natures, différenciées par reset_suivi :
        #   - 'quota'  : fraction du quota_final, suit les resets du parent
        #     (reset_suivi = timestamp du prochain reset, ré-évalué à l'échéance).
        #   - 'budget' : enveloppe fixe SANS reset (reset_suivi = -1), s'épuise.
        # La SOUPLESSE vit ici, par allocation (pas sur budget_final).
        try:
            _add_column_if_missing(self.conn, "budget_final", "alloue",
                                   "REAL DEFAULT 0")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_budget_allocation (
                    allocation_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id         TEXT NOT NULL,
                    adresse_runtime_id INTEGER NOT NULL
                                     REFERENCES adresse_runtime(adresse_runtime_id),
                    budget_final_id  INTEGER NOT NULL REFERENCES budget_final(budget_final_id),
                    nature           TEXT NOT NULL DEFAULT 'quota'
                                     CHECK(nature IN ('quota', 'budget')),
                    fraction         REAL DEFAULT 0,
                    montant          REAL DEFAULT 0,
                    reset_suivi      INTEGER DEFAULT -1,
                    interval_reset   TEXT DEFAULT '',
                    souplesse        TEXT DEFAULT 'strict'
                                     CHECK(souplesse IN ('strict','souple','informatif')),
                    souplesse_taux   REAL DEFAULT 0,
                    spent            REAL DEFAULT 0,
                    status           TEXT NOT NULL DEFAULT 'active'
                                     CHECK(status IN ('active', 'closed')),
                    created_at       INTEGER DEFAULT (strftime('%s','now')),
                    updated_at       INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aba_agent ON agent_budget_allocation(agent_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aba_budget ON agent_budget_allocation(budget_final_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aba_adresse ON agent_budget_allocation(adresse_runtime_id)")
            self.conn.commit()
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            print(f"⚠️  Migration allocation ignorée: {e}")

        # ── Migration calculateur de coûts (Idée 18, section N) : task_level_cost,
        # llm_effort_ratio, llm_task_cost, task_level_stats, llm_level_windows.
        # Le coût d'une action = tokens(par modèle) × temps(par adresse) × prix.
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS task_level_cost (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type_id INTEGER NOT NULL REFERENCES scoring_task_types(id),
                    niveau       TEXT NOT NULL CHECK(niveau IN ('debutant','junior','intermediaire','senior','expert')),
                    travail      REAL DEFAULT 1.0,
                    samples      INTEGER DEFAULT 0,
                    updated_at   INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(task_type_id, niveau)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tlc_type ON task_level_cost(task_type_id)")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_effort_ratio (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id     INTEGER NOT NULL REFERENCES catalogue_models(id),
                    tok_in_par_travail    REAL DEFAULT 1.0,
                    tok_out_par_travail   REAL DEFAULT 1.0,
                    tok_think_par_travail REAL DEFAULT 1.0,
                    req_par_travail       REAL DEFAULT 1.0,
                    temps_par_travail     REAL DEFAULT 1.0,
                    samples      INTEGER DEFAULT 0,
                    updated_at   INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(model_id)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ler_model ON llm_effort_ratio(model_id)")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_task_cost (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id     INTEGER NOT NULL REFERENCES catalogue_models(id),
                    task_type_id INTEGER NOT NULL REFERENCES scoring_task_types(id),
                    niveau       TEXT NOT NULL CHECK(niveau IN ('debutant','junior','intermediaire','senior','expert')),
                    tok_in       REAL DEFAULT 0,
                    tok_out      REAL DEFAULT 0,
                    tok_think    REAL DEFAULT 0,
                    req          REAL DEFAULT 0,
                    temps        REAL DEFAULT 0,
                    thinking_power REAL DEFAULT 0,
                    money        REAL DEFAULT 0,
                    confiance    REAL DEFAULT 0,
                    samples      INTEGER DEFAULT 0,
                    updated_at   INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(model_id, task_type_id, niveau)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ltc_model ON llm_task_cost(model_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ltc_type ON llm_task_cost(task_type_id)")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS task_level_stats (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type_id INTEGER NOT NULL REFERENCES scoring_task_types(id),
                    niveau       TEXT NOT NULL CHECK(niveau IN ('debutant','junior','intermediaire','senior','expert')),
                    ref_niveau   TEXT NOT NULL DEFAULT 'senior',
                    tok_in_ratio REAL DEFAULT 1.0,
                    tok_out_ratio REAL DEFAULT 1.0,
                    tok_think_ratio REAL DEFAULT 1.0,
                    req_ratio    REAL DEFAULT 1.0,
                    temps_ratio  REAL DEFAULT 1.0,
                    samples      INTEGER DEFAULT 0,
                    updated_at   INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(task_type_id, niveau)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tls_type ON task_level_stats(task_type_id)")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_level_windows (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_type_id INTEGER NOT NULL REFERENCES scoring_task_types(id),
                    niveau       TEXT NOT NULL CHECK(niveau IN ('debutant','junior','intermediaire','senior','expert')),
                    tp_min       REAL,
                    tp_max       REAL,
                    tok_in       REAL DEFAULT 0,
                    tok_out      REAL DEFAULT 0,
                    tok_think    REAL DEFAULT 0,
                    req          REAL DEFAULT 0,
                    temps        REAL DEFAULT 0,
                    thinking_power REAL DEFAULT 0,
                    money        REAL DEFAULT 0,
                    updated_at   INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llw_type ON llm_level_windows(task_type_id)")
            self.conn.commit()
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            print(f"⚠️  Migration calculateur ignorée: {e}")

        # ── Migration thinkering_power dérivé (Idée 18, N6) : deux tables,
        # alimentées par le SCOREUR (jamais à l'appel) :
        #   - thinking_power_model : par (model_id, niveau)
        #   - thinking_power_adress : par (adresse_runtime_id, niveau)
        # Formule : Σ_domaines w×score² + Σ_types w×score² (niveau requis).
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS thinking_power_model (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id       INTEGER NOT NULL REFERENCES catalogue_models(id),
                    niveau         TEXT NOT NULL CHECK(niveau IN ('debutant','junior','intermediaire','senior','expert')),
                    thinking_power REAL DEFAULT 0,
                    updated_at     INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(model_id, niveau)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tpm_model ON thinking_power_model(model_id)")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS thinking_power_adress (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    adresse_runtime_id INTEGER NOT NULL REFERENCES adresse_runtime(adresse_runtime_id),
                    niveau         TEXT NOT NULL CHECK(niveau IN ('debutant','junior','intermediaire','senior','expert')),
                    thinking_power REAL DEFAULT 0,
                    updated_at     INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(adresse_runtime_id, niveau)
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tpa_adresse ON thinking_power_adress(adresse_runtime_id)")
            self.conn.commit()
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            print(f"⚠️  Migration thinking_power ignorée: {e}")

        # ── Migration writers par domaine (Idée 18, Q) : tableau des domaines
        # d'écriture métier + le writer principal. Verrou STRICT (tables par
        # domaine) vérifié à l'écriture (repos). Seed idempotent.
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS domain_writers (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    domaine        TEXT NOT NULL UNIQUE,
                    writer_ref     TEXT NOT NULL,
                    tables_json    TEXT DEFAULT '',
                    token_hash     TEXT DEFAULT '',
                    actif          INTEGER DEFAULT 1,
                    description    TEXT DEFAULT '',
                    created_at     INTEGER DEFAULT (strftime('%s','now')),
                    updated_at     INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
            _domain_seed = [
                ("scoring", "scoreur",
                 ["llm_domaine_score", "llm_task_type_score",
                  "score_thinking_power", "thinking_power_model",
                  "thinking_power_adress", "task_level_cost"],
                 "Scores d'expérience + thinking_power dérivé"),
                ("batch", "usage_batcher",
                 ["model_call_log", "model_call_log_archive", "usage_history_1m",
                  "usage_history_15m", "usage_history_3h", "usage_history_1d",
                  "usage_history_1w", "usage_history_1mo", "real_call_models"],
                 "Agrégats temporels + séquences d'usage"),
                ("allocation", "allocateur",
                 ["agent_budget_allocation"],
                 "Claims d'allocation agent→budget"),
                ("budget", "consume_call",
                 ["budget_final", "budget_generique_key_tag",
                  "budget_user_key_id", "cost_final", "cost_key_tag"],
                 "Consommation des budgets/costs à l'appel"),
                ("adresse", "ensure_addresses",
                 ["provider_model_address", "adresse_runtime", "provider_models",
                  "provider_endpoints"],
                 "Référentiel des adresses"),
                ("manifest", "manifest_store",
                 ["?", "config_json"],
                 "Configuration teams/agents (fichiers ouverts)"),
            ]
            import json as _json
            for dom, writer, tables, desc in _domain_seed:
                self.conn.execute("""
                    INSERT OR IGNORE INTO domain_writers
                        (domaine, writer_ref, tables_json, description)
                    VALUES (?, ?, ?, ?)
                """, (dom, writer, _json.dumps(tables), desc))
                # met à jour la liste des tables si elle a évolué
                self.conn.execute("""
                    UPDATE domain_writers SET tables_json = ?, writer_ref = ?
                    WHERE domaine = ?
                """, (_json.dumps(tables), writer, dom))
            self.conn.commit()
        except Exception as e:
            try:
                self.conn.rollback()
            except Exception:
                pass
            print(f"⚠️  Migration domain_writers ignorée: {e}")

        # ── Seed modèles + provider_models si vides ──
        # S'exécute pour TOUTE BDD (vierge OU pré-existante) : le script
        # SQL crée les tables mais ne seede PAS les modèles (ceux-ci
        # viennent de modules/llm_manager/data/*.json). Indépendant des
        # outils — couvre le cas d'un démarrage GUI frais où le
        # catalogue LLM n'a jamais été seedé, OU où seed_catalogue a
        # seedé les modèles mais laissé provider_models vide (transaction
        # ouverte par sync_tools en amont → snapshot vide au seed_provider_models).
        # NB : on commit APRES seed_models pour que provider_models
        # (qui requête catalogue_models) ne voie pas un snapshot vide.
        try:
            mc = self.conn.execute("SELECT COUNT(*) FROM catalogue_models").fetchone()[0]
            pmc = 0
            try:
                pmc = self.conn.execute("SELECT COUNT(*) FROM provider_models").fetchone()[0]
            except Exception:
                pmc = 0
            if mc == 0 or pmc == 0:
                from modules.llm_manager.llm_manager import seed_models, seed_provider_models
                if mc == 0:
                    seed_models(self)
                    self.conn.commit()
                if pmc == 0:
                    seed_provider_models(self)
                    self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Seed modèles ignoré: {e}")
        # Commit final : ferme la transaction implicite des CREATE TABLE
        # ci-dessus (sinon le lock d'écriture WAL reste tenu par le process
        # tant que la connexion vit — daemon, collecteurs, sync…).
        try:
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    @contextmanager
    def transaction(self):
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ── Catalogue aliases : réconciliation noms externes → refs canoniques ──
    def add_alias(self, source: str, target: str, scope: str, alias: str,
                  canonical_ref: str, priority: int = 0) -> Optional[int]:
        """Ajoute ou met à jour un alias (source, target, scope, alias) unique.

        Déclaration passive : si `alias == canonical_ref`, on ne stocke RIEN
        (pas de bruit) et on renvoie None. Sinon upsert + retour de l'id.
        """
        if alias == canonical_ref:
            return None
        cur = self.conn.execute("""
            INSERT INTO catalogue_aliases (source, target, scope, alias, canonical_ref, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, target, scope, alias) DO UPDATE SET
                canonical_ref=excluded.canonical_ref,
                priority=excluded.priority
        """, (source, target, scope, alias, canonical_ref, priority))
        rowid = cur.lastrowid
        cur.fetchall()  # drain le curseur (sqlite reset) avant toute requete suivante
        self.conn.commit()
        return rowid

    def resolve_alias(self, target: str, scope: str, name: str) -> str:
        """Résout un nom `target` vers la ref ModelWeaver (déclaration passive).

        Retourne la ref canonique si un alias matche (priorité max puis id min),
        sinon le `name` d'origine (aucun alias déclaré).
        """
        row = self.conn.execute("""
            SELECT canonical_ref FROM catalogue_aliases
            WHERE target=? AND scope=? AND alias=?
            ORDER BY priority DESC, id ASC LIMIT 1
        """, (target, scope, name)).fetchone()
        return row["canonical_ref"] if row else name

    def alias_map(self, target: str, scope: str) -> Dict[str, str]:
        """Retourne {alias: canonical_ref} pour un target/scope donnés."""
        rows = self.conn.execute("""
            SELECT alias, canonical_ref, priority, id FROM catalogue_aliases
            WHERE target=? AND scope=?
        """, (target, scope)).fetchall()
        out: Dict[str, str] = {}
        # priorité max puis id min en cas de doublon d'alias (ne devrait pas arriver)
        best: Dict[str, tuple] = {}
        for r in rows:
            key = r["alias"]
            score = (r["priority"], -r["id"])
            if key not in best or score > best[key]:
                best[key] = score
                out[key] = r["canonical_ref"]
        return out

    def list_aliases(self, source: Optional[str] = None,
                     target: Optional[str] = None,
                     scope: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = []
        wargs: List[Any] = []
        if source:
            clauses.append("source=?")
            wargs.append(source)
        if target:
            clauses.append("target=?")
            wargs.append(target)
        if scope:
            clauses.append("scope=?")
            wargs.append(scope)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM catalogue_aliases{where} ORDER BY target, scope, alias",
            wargs
        ).fetchall()
        return _rows_to_list(rows)

    def sync_providers(self, rows: List[Dict]) -> int:
        count = 0
        for r in rows:
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_providers
                    (ref, name, provider_type, api_type, website, is_free_tier_provider)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (r["ref"], r["name"], r.get("provider_type"), r.get("api_type"),
                  r.get("website"), r.get("is_free_tier_provider", 0)))
            count += 1
        return count

    def sync_models(self, rows: List[Dict]) -> int:
        count = 0
        for r in rows:
            # Rétro-compat : models.json utilise 'id' au lieu de 'ref'
            ref = r.get("ref") or r.get("id")
            if not ref:
                continue
            name = r.get("name") or ref
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_models
                    (ref, name, developer, release_year, architecture, parameter_count,
                     modality, target_use, license, is_open_weights, parent_model_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ref, name, r.get("developer"), r.get("release_year"),
                  r.get("architecture"), r.get("parameter_count"), r.get("modality"),
                  r.get("target_use"), r.get("license"), r.get("is_open_weights", 0),
                  r.get("parent_model_ref")))
            count += 1
        return count

    def sync_tools(self, rows: List[Dict]) -> int:
        count = 0
        _ensure_classes_outils_table(self.conn)
        for r in rows:
            # classe_outil_id : depuis la classe fournie, sinon déduite du ref
            classe_ref = r.get("classe") or r.get("classe_ref") or _default_class_for_ref(r.get("ref", ""))
            classe_id = resolve_classe_id(self.conn, classe_ref)
            # catalogue_outils
            self.conn.execute("""
                INSERT INTO catalogue_outils (ref, nom, description, tool_type, classe_outil_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET
                    nom=excluded.nom, description=excluded.description,
                    tool_type=COALESCE(excluded.tool_type, catalogue_outils.tool_type),
                    classe_outil_id=COALESCE(excluded.classe_outil_id, catalogue_outils.classe_outil_id)
            """, (r["ref"], r["name"], r.get("description"), r.get("tool_type"), classe_id))
            # catalogue_versions (current_version)
            ver = r.get("current_version")
            if ver:
                oid = self.conn.execute(
                    "SELECT outil_id FROM catalogue_outils WHERE ref=?", (r["ref"],)
                ).fetchone()[0]
                self.conn.execute("""
                    INSERT INTO catalogue_versions (outil_id, nom_version, description)
                    VALUES (?, ?, ?)
                    ON CONFLICT(outil_id, nom_version) DO NOTHING
                """, (oid, ver, r.get("description")))
            count += 1
        return count

    def sync_commands(self, rows: List[Dict]) -> int:
        count = 0
        for r in rows:
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_commands
                    (ref, name, description, command_type)
                VALUES (?, ?, ?, ?)
            """, (r["ref"], r["name"], r.get("description"), r.get("command_type")))
            count += 1
        return count

    def sync_from_url(self, url: str) -> Dict[str, int]:
        """Synchronise toutes les tables catalogue depuis une URL distante.

        L'URL doit exposer les endpoints :
          {url}/providers, {url}/models, {url}/tools, {url}/commands

        Retourne {table: count} des entrées synchronisées.
        """
        import urllib.request
        import json

        endpoints = {
            "providers": self.sync_providers,
            "models": self.sync_models,
            "tools": self.sync_tools,
            "commands": self.sync_commands,
        }
        results = {}

        for name, sync_fn in endpoints.items():
            endpoint = f"{url.rstrip('/')}/{name}"
            try:
                req = urllib.request.Request(endpoint, headers={"User-Agent": "ModelWeaver/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                    if isinstance(data, list):
                        results[name] = sync_fn(data)
                    else:
                        print(f"  ⚠️  {name}: format inattendu")
                        results[name] = 0
                print(f"  ✅ {name}: {results[name]} entrées synchronisées")
            except Exception as e:
                print(f"  ⚠️  {name}: échec synchro ({e})")
                results[name] = -1

        self.conn.commit()
        return results

    def fetch_model_scores(self) -> Dict[str, int]:
        """Lie les scores de model_efficacy (benchmark scraper) aux
        combinaisons provider/modèle de provider_models.

        Crée/met à jour model_provider_scoring avec :
          - quality_score, score_chat, score_coding, score_reasoning,
            score_knowledge, score_agentic, global_score, is_synthetic
          - cost_per_1k_input / cost_per_1k_output depuis provider_models
          - latency_* restent à 0 (remplis par les appels API réels)

        Retourne {table: count} des entrées synchronisées.
        """
        rows = self.conn.execute("""
            SELECT me.model_id, me.model_ref, me.global_score, me.score_quality,
                   me.score_chat, me.score_coding, me.score_reasoning,
                   me.score_knowledge, me.score_agentic,
                   me.is_synthetic, me.samples, me.source_count,
                   pm.provider_id, pm.id AS pm_id,
                   pm.cost_per_input_token, pm.cost_per_output_token
            FROM model_efficacy me
            JOIN catalogue_models cm ON cm.id = me.model_id
            JOIN provider_models pm ON pm.model_id = cm.id
            WHERE me.is_synthetic = 0 OR me.source_count > 0
        """).fetchall()

        count = 0
        for r in rows:
            try:
                cost_in = 0.0
                cost_out = 0.0
                try:
                    cost_in = float(r["cost_per_input_token"] or 0)
                except (ValueError, TypeError):
                    pass
                try:
                    cost_out = float(r["cost_per_output_token"] or 0)
                except (ValueError, TypeError):
                    pass

                self.conn.execute("""
                    INSERT OR REPLACE INTO model_provider_scoring
                    (provider_id, model_id, endpoint_id,
                     quality_score, score_chat, score_coding, score_reasoning,
                     score_knowledge, score_agentic, global_score,
                     is_synthetic, cost_per_1k_input, cost_per_1k_output,
                     benchmark_ref, confidence, last_scored_at, updated_at)
                    VALUES (?, ?, NULL,
                            ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            strftime('%s','now'), strftime('%s','now'))
                """, (
                    r["provider_id"],
                    r["model_id"],
                    r["score_quality"], r["score_chat"], r["score_coding"],
                    r["score_reasoning"], r["score_knowledge"], r["score_agentic"],
                    r["global_score"],
                    r["is_synthetic"],
                    cost_in * 1000, cost_out * 1000,
                    r["model_ref"],
                    0.6 if r["is_synthetic"] else 1.0,
                ))
                count += 1
            except Exception as e:
                print(f"  ⚠ skip scoring {r['model_ref']}: {e}")

        self.conn.commit()
        return {"model_provider_scoring": count}

    def close(self):
        self.conn.close()

    # ── Popularité / télémétrie (compteurs agrégés) ──
    def bump_popularity(self, ref: str, action: str) -> None:
        """Incrémente les compteurs d'install/désinstall pour un outil.

        action: 'install' -> nb_install +1 ; 'uninstall' -> nb_desinstall +1.
        Gestion triviale (+1/-1) ; la réconciliation distante vient plus tard.
        """
        row = self.conn.execute(
            "SELECT outil_id FROM catalogue_outils WHERE ref=?", (ref,)).fetchone()
        if not row:
            return
        oid = row["outil_id"]
        self.conn.execute("INSERT OR IGNORE INTO outils_popularite (outil_id) VALUES (?)", (oid,))
        if action == "install":
            self.conn.execute(
                "UPDATE outils_popularite SET nb_install=nb_install+1, updated_at=strftime('%s','now') WHERE outil_id=?",
                (oid,))
            self.conn.execute(
                """UPDATE catalogue_recettes SET install_count=install_count+1, updated_at=strftime('%s','now')
                   WHERE version_id=(SELECT version_id FROM catalogue_versions WHERE outil_id=?)
                     AND manager='pip' AND enabled=1""", (oid,))
        elif action == "uninstall":
            self.conn.execute(
                "UPDATE outils_popularite SET nb_desinstall=nb_desinstall+1, updated_at=strftime('%s','now') WHERE outil_id=?",
                (oid,))
            self.conn.execute(
                """UPDATE catalogue_recettes SET uninstall_count=uninstall_count+1, updated_at=strftime('%s','now')
                   WHERE version_id=(SELECT version_id FROM catalogue_versions WHERE outil_id=?)
                     AND manager='pip' AND enabled=1""", (oid,))
        self.conn.commit()

    def get_catalogue_tools(self, os_key: str = "linux", arch_key: str = "x86_64") -> dict:
        """Liste les outils du catalogue compatibles avec l'OS/arch local.

        Pour chaque outil on retourne la liste des managers disponibles
        (recettes compatibles), ce qui remplace l'ancien install_method
        (désormais dans catalogue_recettes.manager).

        Chaque outil porte aussi sa classe métier (classe_ref + classe_nom),
        résolue via LEFT JOIN sur classes_outils (fallback 'other').
        """
        cur = self.conn.execute("""
            SELECT DISTINCT o.ref, o.nom, o.description, o.tool_type,
                   c.ref AS classe_ref, c.nom AS classe_nom,
                   r.manager, r.package, r.os, r.arch, r.confidence
            FROM catalogue_outils o
            LEFT JOIN classes_outils c ON c.classe_id = o.classe_outil_id
            JOIN catalogue_versions v ON v.outil_id = o.outil_id
            JOIN catalogue_recettes r ON r.version_id = v.version_id
            WHERE r.os IN (?, 'all') AND r.arch IN (?, 'all') AND r.enabled = 1
            ORDER BY o.nom, r.manager
        """, (os_key, arch_key))
        cols = [d[0] for d in cur.description]
        tools_map = {}
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            ref = d["ref"]
            if ref not in tools_map:
                classe_ref = d["classe_ref"] or _default_class_for_ref(ref)
                classe_nom = d["classe_nom"] or classe_ref
                tools_map[ref] = {
                    "ref": ref, "name": d["nom"],
                    "description": d["description"],
                    "tool_type": d["tool_type"],
                    "classe_ref": classe_ref,
                    "classe": classe_nom,
                    "managers": [],
                }
            tools_map[ref]["managers"].append({
                "manager": d["manager"], "package": d["package"],
                "os": d["os"], "arch": d["arch"], "confidence": d["confidence"],
            })
        tools = list(tools_map.values())
        return {"tools": tools, "count": len(tools)}


# ──────────────────────────────────────────────
#  RuntimeDB : écritures haute fréquence
# ──────────────────────────────────────────────
