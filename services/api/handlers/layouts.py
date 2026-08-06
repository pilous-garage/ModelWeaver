"""Routes layout/*, session/*, theme/*, panel/list.

Gère les layouts, sessions, thèmes et l'inventaire des panels
natifs dans ~/.modelweaver/.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from services._common import mw_home
from services.api.router import register

_LAYOUTS_DIR = mw_home() / "layouts"
_SESSIONS_DIR = mw_home() / "sessions"
_THEMES_DIR = mw_home() / "themes"
_DEFAULT_LAYOUTS = Path(__file__).resolve().parent.parent.parent.parent / "interfaces" / "defaults" / "layouts"


def _ensure_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)


def _list_json_dir(d: Path, default_dir: Optional[Path] = None) -> list:
    _ensure_dir(d)
    seen = set()
    result = []
    # Chercher d'abord dans les defaults, puis dans le répertoire user
    for source_dir in ([default_dir, d] if default_dir else [d]):
        if not source_dir or not source_dir.exists():
            continue
        for p in sorted(source_dir.glob("*.json")):
            name = p.stem
            if name in seen:
                continue
            seen.add(name)
            try:
                data = json.loads(p.read_text())
                label = data.get("label", data.get("id", name))
            except Exception:
                label = name
            result.append({"name": name, "label": label, "path": str(p), "default": source_dir == default_dir})
    return result


def _get_json(d: Path, name: str, default_dir: Optional[Path] = None) -> Optional[Dict]:
    _ensure_dir(d)
    for ext in (".json",):
        # D'abord le répertoire user, puis les defaults
        for source_dir in ([d, default_dir] if default_dir else [d]):
            if not source_dir:
                continue
            p = source_dir / f"{name}{ext}"
            if p.exists():
                return {"name": name, "yaml": p.read_text(), "path": str(p), "default": source_dir == default_dir}
    return None


def _save_json(d: Path, name: str, content: str) -> Dict:
    _ensure_dir(d)
    p = d / f"{name}.json"
    # Écriture ATOMIQUE : tmp + rename. Les mutations GUI (ex. drag continu)
    # déclenchent plusieurs layout/save concurrents ; sans atomicité, deux
    # write_text parallèles se chevauchent et corrompent le fichier.
    tmp = d / f".{name}.json.tmp"
    tmp.write_text(content, encoding="utf-8")
    import os
    os.replace(tmp, p)
    return {"ok": True, "path": str(p)}


def _delete_json(d: Path, name: str) -> Dict:
    p = d / f"{name}.json"
    if p.exists():
        p.unlink()
        return {"ok": True}
    return {"ok": False, "error": "fichier introuvable"}


# ── Layouts ────────────────────────────────────────────────

def op_layout_list(params: dict) -> Dict[str, Any]:
    return {"layouts": _list_json_dir(_LAYOUTS_DIR, _DEFAULT_LAYOUTS), "count": 0}


def op_layout_get(params: dict) -> Dict[str, Any]:
    name = params.get("name", "")
    if not name:
        return {"error": "name requis"}
    result = _get_json(_LAYOUTS_DIR, name, _DEFAULT_LAYOUTS)
    if not result:
        return {"error": f"layout '{name}' introuvable"}
    return result


def op_layout_save(params: dict) -> Dict[str, Any]:
    name = params.get("name", "")
    yaml_content = params.get("yaml", "")
    if not name or not yaml_content:
        return {"error": "name et yaml requis"}
    return _save_json(_LAYOUTS_DIR, name, yaml_content)


def op_layout_delete(params: dict) -> Dict[str, Any]:
    name = params.get("name", "")
    if not name:
        return {"error": "name requis"}
    return _delete_json(_LAYOUTS_DIR, name)


# ── Sessions ───────────────────────────────────────────────

def op_session_list(params: dict) -> Dict[str, Any]:
    return {"sessions": _list_json_dir(_SESSIONS_DIR), "count": 0}


def op_session_get(params: dict) -> Dict[str, Any]:
    name = params.get("name", "")
    if not name:
        return {"error": "name requis"}
    result = _get_json(_SESSIONS_DIR, name)
    if not result:
        return {"error": f"session '{name}' introuvable"}
    return result


def op_session_save(params: dict) -> Dict[str, Any]:
    name = params.get("name", "")
    yaml_content = params.get("yaml", "")
    if not name or not yaml_content:
        return {"error": "name et yaml requis"}
    return _save_json(_SESSIONS_DIR, name, yaml_content)


def op_session_delete(params: dict) -> Dict[str, Any]:
    name = params.get("name", "")
    if not name:
        return {"error": "name requis"}
    return _delete_json(_SESSIONS_DIR, name)


# ── Thèmes ────────────────────────────────────────────────

def op_theme_list(params: dict) -> Dict[str, Any]:
    return {"themes": _list_json_dir(_THEMES_DIR), "count": 0}


def op_theme_get(params: dict) -> Dict[str, Any]:
    name = params.get("name", "")
    if not name:
        return {"error": "name requis"}
    result = _get_json(_THEMES_DIR, name)
    if not result:
        return {"error": f"theme '{name}' introuvable"}
    return result


def op_theme_save(params: dict) -> Dict[str, Any]:
    name = params.get("name", "")
    yaml_content = params.get("yaml", "")
    if not name or not yaml_content:
        return {"error": "name et yaml requis"}
    return _save_json(_THEMES_DIR, name, yaml_content)


# ── Panels ────────────────────────────────────────────────

_PANELS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "interfaces" / "main" / "GUI" / "official" / "gui" / "src" / "panels"


def op_panel_list(params: dict) -> Dict[str, Any]:
    """Liste les panels natifs disponibles.

    Parcourt le code source à la recherche des fichiers .panel.tsx.
    Utilise la déclaration() de chaque panneau si disponible.
    """
    panels = []
    for f in sorted(_PANELS_DIR.rglob("*.panel.tsx")):
        rel = f.relative_to(_PANELS_DIR)
        parts = str(rel.parent / rel.stem.replace(".panel", "")).replace("/", "-").lower()
        panels.append({
            "id": parts,
            "file": str(rel),
        })
    return {"panels": panels, "count": len(panels)}


# ── Route registration ────────────────────────────────────

register("layout/list",   op_layout_list)
register("layout/get",    op_layout_get)
register("layout/save",   op_layout_save)
register("layout/delete", op_layout_delete)

register("session/list",   op_session_list)
register("session/get",    op_session_get)
register("session/save",   op_session_save)
register("session/delete", op_session_delete)

register("theme/list",   op_theme_list)
register("theme/get",    op_theme_get)
register("theme/save",   op_theme_save)

register("panel/list",   op_panel_list)
