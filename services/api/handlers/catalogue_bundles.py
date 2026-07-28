"""Routes catalogue/bundles/* — Gestion des bundles YAML.

Les bundles sont dans ``AgentsCatalogue/bundles/*.yaml``.
Chaque bundle définit une liste de skills + des permissions.
"""

from pathlib import Path
from typing import Any, Dict

from services._common import mw_home
from services.api.router import register

_BUNDLES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "AgentsCatalogue" / "bundles"


def _resolve_bundles_dir() -> Path:
    """Retourne le dossier des bundles. Cherche d'abord sous mw_home(),
    puis sous le repo."""
    candidates = [
        mw_home() / "AgentsCatalogue" / "bundles",
        _BUNDLES_DIR,
    ]
    for p in candidates:
        if p.exists():
            return p
    return _BUNDLES_DIR


def op_bundles_list(params: dict) -> Dict[str, Any]:
    """Liste tous les bundles disponibles.

    Retourne pour chaque bundle : nom, description, skills count.
    """
    bundles_dir = _resolve_bundles_dir()
    result = []
    for yaml_path in sorted(bundles_dir.glob("*.yaml")):
        name = yaml_path.stem
        try:
            import yaml
            data = yaml.safe_load(yaml_path.read_text())
            skills = data.get("skills", []) if data else []
            permissions = data.get("permissions", []) if data else []
            result.append({
                "name": name,
                "description": (data or {}).get("description", ""),
                "skill_count": len(skills),
                "permission_count": len(permissions),
                "path": str(yaml_path.relative_to(bundles_dir.parent.parent) if bundles_dir.parent.parent else yaml_path),
            })
        except Exception as e:
            result.append({
                "name": name,
                "description": f"Erreur de chargement: {e}",
                "skill_count": 0,
                "permission_count": 0,
                "error": str(e),
            })
    return {"bundles": result, "count": len(result)}


def op_bundles_get(params: dict) -> Dict[str, Any]:
    """Retourne le contenu YAML d'un bundle."""
    name = params.get("name", "")
    if not name:
        return {"error": "name requis"}
    bundles_dir = _resolve_bundles_dir()
    for ext in (".yaml", ".yml"):
        path = bundles_dir / f"{name}{ext}"
        if path.exists():
            content = path.read_text()
            return {"name": name, "yaml": content, "path": str(path)}
    return {"error": f"Bundle '{name}' introuvable"}


def op_bundles_save(params: dict) -> Dict[str, Any]:
    """Sauvegarde un bundle YAML."""
    name = params.get("name", "")
    yaml_content = params.get("yaml", "")
    if not name or not yaml_content:
        return {"error": "name et yaml requis"}
    bundles_dir = _resolve_bundles_dir()
    bundles_dir.mkdir(parents=True, exist_ok=True)
    path = bundles_dir / f"{name}.yaml"
    path.write_text(yaml_content)
    return {"ok": True, "path": str(path)}


register("catalogue/bundles/list", op_bundles_list)
register("catalogue/bundles/get", op_bundles_get)
register("catalogue/bundles/save", op_bundles_save)
