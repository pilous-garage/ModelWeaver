#!/usr/bin/env python3
"""ModelWeaver — Data Access Layer.

Repositories pour les bases modelweaver.db et catalogue.db.
Les modules métier n'écrivent jamais de SQL directement.
"""

import json
import sqlite3
import uuid
import os
import libsql_client as libsql
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from sql.migrations import MigrationManager
from dotenv import load_dotenv

load_dotenv()


def _ref(prefix: str = "key") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_local_db() -> Path:
    return _project_root() / ".modelweaver" / "modelweaver.db"


def _default_catalogue_db() -> Path:
    return _project_root() / ".modelweaver" / "catalogue.db"


# ──────────────────────────────────────────────
#  Utility
# ──────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────
#  Repositories
# ──────────────────────────────────────────────

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
                    rate_limits_json=?, metadata_json=?, updated_at=strftime('%s','now')
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
                existing["id"]
            ))
            return existing["id"]

        cur = self.conn.execute("""
            INSERT INTO provider_models (provider_id, model_id, provider_model_name,
                context_window_tokens, max_output_tokens, cost_per_input_token,
                cost_per_output_token, cost_billing, pricing_rules_json,
                limits_json, rate_limits_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            (extra or {}).get("metadata_json")
        ))
        return cur.lastrowid


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
            AND ak.health_status IN ('unknown', 'ok', 'degraded')
            ORDER BY ak.health_status = 'ok' DESC, ak.health_status = 'unknown' DESC
            LIMIT 1
        """, (provider_ref, identity))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> str:
        ref = data.get("ref") or _ref()
        cur = self.conn.execute("""
            INSERT INTO api_keys (ref, identity, provider_id, key_value, tag, grade,
                health_status, expiration_date, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ref, data.get("identity", "default"),
            data["provider_id"], data["key_value"],
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

    def delete(self, ref: str) -> bool:
        cur = self.conn.execute("DELETE FROM api_keys WHERE ref = ?", (ref,))
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


class ToolRepository:
    """Gestion des outils (Définitions et Variantes)."""
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, tool_type: Optional[str] = None,
                  is_core: Optional[bool] = None) -> List[Dict[str, Any]]:
        """Liste les outils en joignant définitions et variantes."""
        clauses = []
        params = []
        if tool_type:
            clauses.append("v.manager = ?")
            params.append(tool_type)
        if is_core is not None:
            # Note: is_core devra être ajouté à tool_definitions ou tool_variants
            # Pour l'instant on ignore ou on utilise is_official
            clauses.append("v.is_official = ?")
            params.append(1 if is_core else 0)
            
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = f"""
            SELECT d.ref, d.name, d.description, d.tool_class, 
                   v.version, v.manager, v.os, v.architecture,
                   v.size_download, v.size_disk, v.trust_score, 
                   v.install_count, v.uninstall_count
            FROM tool_definitions d
            JOIN tool_variants v ON d.id = v.tool_id
            {where}
            ORDER BY d.name
        """
        cur = self.conn.execute(query, params)
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        """Récupère l'outil et sa variante la plus populaire."""
        cur = self.conn.execute("""
            SELECT d.*, v.* 
            FROM tool_definitions d 
            JOIN tool_variants v ON d.id = v.tool_id 
            WHERE d.ref = ? 
            ORDER BY v.install_count DESC LIMIT 1
        """, (ref,))
        row = cur.fetchone()
        return _row_to_dict(row) if row else None

    def save(self, data: Dict[str, Any]) -> int:
        """Enregistre ou met à jour un outil et sa variante par défaut."""
        ref = data.get("ref") or _ref("tool")
        
        # 1. Definition
        self.conn.execute("""
            INSERT INTO tool_definitions (ref, name, description, tool_class)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ref) DO UPDATE SET
                name=excluded.name, description=excluded.description, tool_class=excluded.tool_class
        """, (ref, data.get("name"), data.get("description"), data.get("class", "other")))
        
        cur = self.conn.execute("SELECT id FROM tool_definitions WHERE ref = ?", (ref,))
        tool_id = cur.fetchone()[0]
        
        # 2. Variante par défaut (all/all)
        self.conn.execute("""
            INSERT INTO tool_variants (tool_id, os, architecture, version, manager, size_download, size_disk, trust_score)
            VALUES (?, 'all', 'all', ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                version=excluded.version, manager=excluded.manager, 
                size_download=excluded.size_download, size_disk=excluded.size_disk,
                trust_score=excluded.trust_score
        """, (tool_id, data.get("current_version"), data.get("install_method"), 
              data.get("size_download", 0), data.get("size_disk", 0), data.get("trust_score", 1.0)))
        
        return tool_id

    def update_metrics(self, ref: str, os: str, arch: str, manager: str, 
                       size_dl: int, size_dk: int, install: bool = True):
        """Met à jour les stats d'une variante spécifique."""
        cur = self.conn.execute("SELECT id FROM tool_definitions WHERE ref = ?", (ref,))
        row = cur.fetchone()
        if not row: return
        tool_id = row[0]
        
        self.conn.execute("""
            INSERT INTO tool_variants (tool_id, os, architecture, manager, size_download, size_disk)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                size_download=excluded.size_download, size_disk=excluded.size_disk
        """, (tool_id, os, arch, manager, size_dl, size_dk))
        
        col = "install_count" if install else "uninstall_count"
        self.conn.execute(f"UPDATE tool_variants SET {col} = {col} + 1 WHERE tool_id = ? AND os = ? AND architecture = ? AND manager = ?", 
                          (tool_id, os, arch, manager))

    def scan_installed(self, local_repo: "LocalToolRepository") -> int:
        """Détecte les outils installés et peuple local_tools."""
        import shutil, subprocess, sys
        count = 0
        binaries = {
            "ollama": ("ollama", "--version"),
            "opencode": ("opencode", "--version"),
            "python3": ("python3", "--version"),
            "git": ("git", "--version"),
            "curl": ("curl", "--version"),
        }
        for ref, (cmd, ver_flag) in binaries.items():
            path = shutil.which(cmd)
            if not path: continue
            version = None
            try:
                r = subprocess.run([cmd, ver_flag], capture_output=True, text=True, timeout=5)
                version = r.stdout.strip().split()[-1] if r.stdout else None
            except Exception: pass
            
            self.save({"ref": ref, "name": ref, "install_method": "binary", "current_version": version})
            cur = self.conn.execute("SELECT id FROM tool_definitions WHERE ref = ?", (ref,))
            tool = cur.fetchone()
            if tool:
                local_repo.save({"tool_id": tool[0], "version": version or "unknown", "install_path": path, "status": "installed"})
                count += 1
        
        pip_tools = {"litellm": "litellm", "open-webui": "open_webui", "gitingest": "gitingest"}
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json"], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                import json
                pip_packages = json.loads(r.stdout)
                pip_map = {p["name"].lower().replace("-", "_"): p["version"] for p in pip_packages}
                for ref, pkg_name in pip_tools.items():
                    version = pip_map.get(pkg_name.lower())
                    if not version: continue
                    self.save({"ref": ref, "name": ref, "install_method": "pip", "current_version": version})
                    cur = self.conn.execute("SELECT id FROM tool_definitions WHERE ref = ?", (ref,))
                    tool = cur.fetchone()
                    if tool:
                        local_repo.save({"tool_id": tool[0], "version": version, "install_path": sys.executable, "status": "installed"})
                        count += 1
        except Exception: pass
        return count



class LocalToolRepository:
    """Outils installés localement."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            cur = self.conn.execute("""
                SELECT lt.*, d.ref as tool_ref, d.name as tool_name
                FROM local_tools lt JOIN tool_definitions d ON d.id = lt.tool_id
                WHERE lt.status = ? ORDER BY d.name
            """, (status,))
        else:
            cur = self.conn.execute("""
                SELECT lt.*, d.ref as tool_ref, d.name as tool_name
                FROM local_tools lt JOIN tool_definitions d ON d.id = lt.tool_id
                ORDER BY d.name
            """)
        return _rows_to_list(cur.fetchall())

    def save(self, data: Dict[str, Any]) -> int:
        tool_id = data.get("tool_id")
        if not tool_id:
            raise ValueError("tool_id requis")
        cur = self.conn.execute(
            "SELECT id FROM local_tools WHERE tool_id = ?", (tool_id,)
        )
        existing = cur.fetchone()
        if existing:
            self.conn.execute("""
                UPDATE local_tools SET version=?, install_path=?, status=?,
                    updated_at=strftime('%s','now')
                WHERE id=?
            """, (data.get("version"), data.get("install_path"),
                  data.get("status", "installed"), existing["id"]))
            return existing["id"]
        cur = self.conn.execute("""
            INSERT INTO local_tools (tool_id, version, install_path, status, installed_at)
            VALUES (?, ?, ?, ?, ?)
        """, (tool_id, data.get("version"), data.get("install_path"),
              data.get("status", "installed"),
              data.get("installed_at") if data.get("installed_at") else None))
        return cur.lastrowid


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

class ToolClassRepository:
    """Classes/catégories d'outils."""
    
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self) -> List[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM tool_classes ORDER BY sort_order, label")
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM tool_classes WHERE ref = ?", (ref,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        ref = data.get("ref")
        existing = None
        if ref:
            cur = self.conn.execute("SELECT id FROM tool_classes WHERE ref = ?", (ref,))
            existing = cur.fetchone()
        if existing:
            self.conn.execute("""
                UPDATE tool_classes SET label=?, sort_order=?
                WHERE id=?
            """, (data.get("label"), data.get("sort_order", 0), existing["id"]))
            return existing["id"]
        cur = self.conn.execute(
            "INSERT INTO tool_classes (ref, label, sort_order) VALUES (?, ?, ?)",
            (ref, data.get("label"), data.get("sort_order", 0))
        )
        return cur.lastrowid

    def delete(self, ref: str) -> bool:
        cur = self.conn.execute("DELETE FROM tool_classes WHERE ref = ?", (ref,))
        return cur.rowcount > 0


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
            self.client = libsql.create_client_sync(self.url.replace("libsql://", "https://"), auth_token=self.token)
        except (ImportError, AttributeError):
            try:
                import libsql_client as libsql
                self.client = libsql.create_client_sync(self.url.replace("libsql://", "https://"), auth_token=self.token)
            except Exception as e:
                raise RuntimeError(f"Échec de connexion Turso: {e}")

    def get_tool(self, ref: str) -> Optional[Dict[str, Any]]:
        """Récupère un outil spécifique depuis le catalogue distant."""
        try:
            res = self.client.execute("SELECT * FROM catalogue_tools WHERE ref = ?", (ref,))
            if res and len(res) > 0:
                row = res[0]
                if hasattr(res, 'columns'):
                    return dict(zip(res.columns, row))
                return dict(row) if not isinstance(row, (tuple, list)) else None
            return None
        except Exception as e:
            print(f"❌ Erreur Turso (get_tool): {e}")
            return None

    def list_tools(self, tool_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Liste les outils avec filtre optionnel."""
        query = "SELECT * FROM catalogue_tools"
        params = []
        if tool_type:
            query += " WHERE tool_type = ?"
            params.append(tool_type)
        query += " ORDER BY name"
        
        try:
            res = self.client.execute(query, params)
            return [dict(row) for row in res]
        except Exception as e:
            print(f"❌ Erreur Turso (list_tools): {e}")
            return []

    def update_metrics(self, tool_id: int, version: str, os_name: str, arch: str, 
                      manager: str, size_download: int, size_disk: int) -> bool:
        """Enregistre les poids mesurés après installation."""
        sql = """
        INSERT INTO tool_metrics (tool_id, version, os, arch, manager, size_download, size_disk)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tool_id, version, os, arch, manager) 
        DO UPDATE SET size_download=excluded.size_download, 
                      size_disk=excluded.size_disk, 
                      last_measured=strftime('%s','now')
        """
        try:
            self.client.execute(sql, (tool_id, version, os_name, arch, manager, size_download, size_disk))
            return True
        except Exception as e:
            print(f"❌ Erreur Turso (update_metrics): {e}")
            return False

    def upsert_tool(self, tool_data: Dict[str, Any]) -> bool:
        """Ajoute ou met à jour un outil dans le catalogue distant."""
        sql = """
        INSERT INTO catalogue_tools (ref, name, description, tool_type, install_method, current_version, 
                                    default_download_url, allowed_platforms, allowed_arches)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ref) DO UPDATE SET
            name=excluded.name, description=excluded.description, tool_type=excluded.tool_type,
            install_method=excluded.install_method, current_version=excluded.current_version,
            default_download_url=excluded.default_download_url, allowed_platforms=excluded.allowed_platforms,
            allowed_arches=excluded.allowed_arches
        """
        params = (
            tool_data.get("ref"), tool_data.get("name"), tool_data.get("description"),
            tool_data.get("tool_type"), tool_data.get("install_method"), tool_data.get("current_version"),
            tool_data.get("default_download_url"), tool_data.get("allowed_platforms"), tool_data.get("allowed_arches")
        )
        try:
            self.client.execute(sql, params)
            return True
        except Exception as e:
            print(f"❌ Erreur Turso (upsert_tool): {e}")
            return False


# ──────────────────────────────────────────────
#  Main DB class
# ──────────────────────────────────────────────


class AgentDBMixin:
    """Agent OS repositories (importés séparément pour éviter les dépendances circulaires)."""

    def _init_agent_repos(self):
        from sql.agent_repository import (
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
        from sql.orchestration_repository import (
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
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema()
        
        # Ajout du catalogue distant
        try:
            self.remote_catalogue = TursoCatalogueDB()
        except Exception as e:
            print(f"⚠️  Mode catalogue distant désactivé: {e}")
            self.remote_catalogue = None

        # Appliquer les migrations SQL
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        if migrations_dir.exists():
            manager = MigrationManager(self.db_path)
            manager.apply_migrations(migrations_dir)

        self.providers = ProviderRepository(self.conn)
        self.models = ModelRepository(self.conn)
        self.keys = KeyRepository(self.conn)
        self.tools = ToolRepository(self.conn)
        self.tool_classes = ToolClassRepository(self.conn)
        self.system_state = SystemStateRepository(self.conn)
        self.local_tools = LocalToolRepository(self.conn)
        self.llms = LocalLLMRepository(self.conn)
        self.commands = CommandRepository(self.conn)
        self._init_agent_repos()
        self._init_orchestration_repos()
        from sql.agent_repository import ScheduledJobRepository
        self.scheduled_jobs = ScheduledJobRepository(self.conn)

    def _ensure_schema(self):
        """Crée les tables si elles n'existent pas encore.

        Applique tout le schema à chaque connexion (sûr grâce à IF NOT EXISTS).
        """
        schema = Path(__file__).resolve().parent / "modelweaver_schema.sql"
        if schema.exists():
            self.conn.executescript(schema.read_text())

        # Migration: ajouter recipe_path si manquant
        try:
            self.conn.execute("ALTER TABLE tools ADD COLUMN recipe_path TEXT")
        except Exception:
            pass

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

    def scan_installed_tools(self) -> int:
        """Détecte les outils installés et met à jour local_tools."""
        count = self.tools.scan_installed(self.local_tools)
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
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._ensure_schema()

    def _ensure_schema(self):
        """Crée les tables si elles n'existent pas encore."""
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='catalogue_providers'"
        )
        if not cur.fetchone():
            schema = Path(__file__).resolve().parent / "catalogue_schema.sql"
            if schema.exists():
                self.conn.executescript(schema.read_text())

    @contextmanager
    def transaction(self):
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

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
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_models
                    (ref, name, developer, release_year, architecture, parameter_count,
                     modality, target_use, license, is_open_weights, parent_model_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (r["ref"], r["name"], r.get("developer"), r.get("release_year"),
                  r.get("architecture"), r.get("parameter_count"), r.get("modality"),
                  r.get("target_use"), r.get("license"), r.get("is_open_weights", 0),
                  r.get("parent_model_ref")))
            count += 1
        return count

    def sync_tools(self, rows: List[Dict]) -> int:
        count = 0
        for r in rows:
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_tools
                    (ref, name, description, tool_type, install_method, current_version,
                     default_download_url, allowed_platforms, allowed_arches)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (r["ref"], r["name"], r.get("description"), r.get("tool_type"),
                  r.get("install_method"), r.get("current_version"),
                  r.get("default_download_url"), r.get("allowed_platforms"),
                  r.get("allowed_arches")))
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

    def close(self):
        self.conn.close()


# ──────────────────────────────────────────────
#  Quick test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    db = ModelWeaverDB()
    print(f"🔌 Connecté à {db.db_path}")

    print(f"\n  Providers : {len(db.providers.list_all())}")
    print(f"  Modèles   : {len(db.models.list_all())}")
    print(f"  Clés      : {len(db.keys.list_all())}")
    print(f"  LLMs locaux: {len(db.llms.list_all())}")
    print(f"  Commandes : {len(db.commands.list_all())}")

    g = db.providers.get("groq")
    print(f"\n  groq → {g}")

    print("\n  Recherche 'gemini':")
    for m in db.models.search("gemini", modality="text"):
        print(f"    → {m['ref']} ({m['developer']})")

    db.close()

    cat = CatalogueDB()
    print(f"\n🔌 Catalogue: {cat.db_path}")
    cat.close()
