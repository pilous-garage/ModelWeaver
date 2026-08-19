"""catalogue_bridge — recâblage installer_worker vers les domaines sqlite.

Remplace les lectures depuis catalogue_outils (legacy catalogue.db) et les
écritures tool_usage (legacy modelweaver.db) par :
  - lookup_tool(ref)      → catalogue local data_type 'tool'
  - record_usage(ref,...) → hardware_software.installed_software

install_tool/uninstall_tool continuent d'utiliser RecipeParser (legacy fs)
mais récupèrent la déclaration outil + tracent l'install via ces fonctions.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from modules.sqlite.local import db as local_db, data_table
from modules.sqlite.hardware_software import db as hw_db, write as hw_write


def lookup_tool(ref: str) -> Optional[Dict[str, Any]]:
    """Lit un outil depuis le catalogue local 'tool' (remplace catalogue_outils)."""
    ld = local_db()
    try:
        dt = data_table.get_table(ld, "tool")
        row = dt.get(ref)
        return {
            "ref": ref,
            "name": row.get("name", ref),
            "description": row.get("description", ""),
            "version_json": row.get("version_json", "{}"),
            "cmd_json": row.get("cmd_json", "{}"),
            "hash_sha256": row.get("hash_sha256", ""),
            "hash_md5": row.get("hash_md5", ""),
            "tags": row.get("tags", []),
        }
    except (KeyError, Exception):
        return None
    finally:
        ld.close()


def record_usage(tool_ref: str, etat: str, path_installed: str = "",
                 cmd_json: str = "{}", recipe_ref: str = "",
                 privilege_tag: str = "default") -> Dict[str, Any]:
    """Trace l'install local dans hardware_software.installed_software
    (remplace tool_usage dans modelweaver.db)."""
    d = hw_db()
    try:
        status = "installed" if etat == "installed" else (
            "missing" if etat == "uninstalled" else "updated")
        r = hw_write.register_software(
            d, tool_ref=tool_ref, name=tool_ref, recipe_ref=recipe_ref,
            cmd_json=cmd_json, path_installed=path_installed, status=status,
            privilege_tag=privilege_tag)
        return r
    finally:
        d.close()
