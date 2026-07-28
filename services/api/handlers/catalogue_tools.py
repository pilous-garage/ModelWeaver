"""Routes catalogue/tools/* — Gestion des outils Registry YAML.

Les outils sont dans ``AgentsCatalogue/tools/*.tool.yaml``.
Ce sont les outils auto-découverts (delegate, edit, grep…).
"""

from pathlib import Path
from typing import Any, Dict

from services._common import mw_home
from services.api.router import register

_TOOLS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "AgentsCatalogue" / "tools"


def _resolve_tools_dir() -> Path:
    candidates = [
        mw_home() / "AgentsCatalogue" / "tools",
        _TOOLS_DIR,
    ]
    for p in candidates:
        if p.exists():
            return p
    return _TOOLS_DIR


def op_tools_list(params: dict) -> Dict[str, Any]:
    """Liste tous les outils Registry YAML disponibles."""
    tools_dir = _resolve_tools_dir()
    result = []
    for yaml_path in sorted(tools_dir.glob("*.tool.yaml")):
        try:
            import yaml
            data = yaml.safe_load(yaml_path.read_text())
            result.append({
                "name": (data or {}).get("name", yaml_path.stem.replace(".tool", "")),
                "description": (data or {}).get("description", ""),
                "parameters": list((data or {}).get("parameters", {}).get("properties", {}).keys()),
                "required": (data or {}).get("parameters", {}).get("required", []),
                "implementation": (data or {}).get("implementation", {}).get("function", ""),
                "path": str(yaml_path),
            })
        except Exception as e:
            result.append({
                "name": yaml_path.stem.replace(".tool", ""),
                "error": str(e),
            })
    return {"tools": result, "count": len(result)}


def op_tools_get(params: dict) -> Dict[str, Any]:
    """Retourne le contenu YAML d'un outil."""
    name = params.get("name", "")
    if not name:
        return {"error": "name requis"}
    tools_dir = _resolve_tools_dir()
    path = tools_dir / f"{name}.tool.yaml"
    if path.exists():
        content = path.read_text()
        return {"name": name, "yaml": content, "path": str(path)}
    return {"error": f"Tool '{name}' introuvable"}


register("catalogue/tools/list", op_tools_list)
register("catalogue/tools/get", op_tools_get)
