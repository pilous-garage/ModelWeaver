"""Routes daemon — panels GUI (extensions compilées) et fenêtres.

Sert la liste des panels (index), l'état (status), et délègue le servie des
fichiers JS compilés au daemon (panels/file/<id>). Le chargement se fait côté
GUI via import() dynamique.

Fenêtres : templates de base + lister/ouvrir.
"""

from pathlib import Path

from services.api.router import register
from services._common import mw_home

DIST_DIR = mw_home() / "panels-dist"
REGISTRY = mw_home() / "panels" / "index.json"


def _load_registry() -> list:
    try:
        import json
        return json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else []
    except Exception:
        return []


def op_panels_index(params):
    """Liste des panels externes enregistrés (compilés par panel-creator)."""
    return {"status": "ok", "panels": _load_registry(), "count": len(_load_registry())}


def op_panels_status(params):
    """Santé des panels : fichier présent, taille."""
    out = []
    for entry in _load_registry():
        fname = entry.get("file", "").split("/")[-1] or f"{entry['id']}.js"
        f = DIST_DIR / fname
        out.append({**entry, "exists": f.exists(),
                    "size": f.stat().st_size if f.exists() else 0})
    return {"status": "ok", "count": len(out), "panels": out}


def op_panels_file(params):
    """Retourne le contenu JS d'un panel compilé (servi comme module ES).

    Le daemon ajoute le Content-Type application/javascript pour que la GUI
    puisse faire import(url). Renvoie {status, id, js} — le daemon peut aussi
    servir le fichier brut selon l'implémentation.
    """
    pid = params.get("id", "")
    if not pid:
        return {"status": "error", "error": "id requis"}
    f = DIST_DIR / f"{pid}.js"
    if not f.exists():
        return {"status": "error", "error": f"panel '{pid}' non compilé"}
    return {"status": "ok", "id": pid, "js": f.read_text(encoding="utf-8"),
            "content_type": "application/javascript"}


def op_panels_build(params):
    """Recompile les panneaux non-essentiels (panel-creator)."""
    try:
        from services.panel_creator.panel_creator import build_all
        force = bool(params.get("force", False))
        res = build_all(force=force)
        return {"status": "ok", **res}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Bundles de panels ────────────────────────────────────────────────


def op_panels_bundles_list(params):
    """Liste les bundles de panels (groupements)."""
    try:
        from services.panel_creator.panel_creator import list_bundles
        bundles = list_bundles()
        return {"status": "ok", "bundles": bundles, "count": len(bundles)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_panels_bundles_get(params):
    """Retourne un bundle (avec la liste de ses panels)."""
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    try:
        from services.panel_creator.panel_creator import get_bundle
        b = get_bundle(name)
        if not b:
            return {"status": "error", "error": f"bundle inconnu: {name}"}
        return {"status": "ok", "bundle": b}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_panels_bundles_build(params):
    """Compile les panels EXTERNES d'un bundle."""
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    try:
        from services.panel_creator.panel_creator import build_bundle
        force = bool(params.get("force", False))
        res = build_bundle(name, force=force)
        return {"status": "ok", **res}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Fenêtres (gestionnaire persistant) ───────────────────────────────
# Chaque fenêtre = un profil JSON dans ~/.modelweaver/windows/<id>.json :
# { window_id, template, layout, theme, title, x, y, width, height,
#   state (normal|maximized|minimized), visible, opened_at }
# La GUI crée les vraies fenêtres Tauri (create_window) et persiste leur
# position/taille via windows/update.

WINDOW_TEMPLATES = {
    "dashboard": {
        "label": "Dashboard",
        "icon": "dashboard",
        "layout": "dashboard",
        "theme": "dark",
        "defaultSize": {"width": 1200, "height": 800},
        "defaultPos": {"x": None, "y": None},
    },
    "default": {
        "label": "Par défaut",
        "icon": "window",
        "layout": "default",
        "theme": "dark",
        "defaultSize": {"width": 1000, "height": 700},
        "defaultPos": {"x": None, "y": None},
    },
    "agentIde": {
        "label": "Agent IDE",
        "icon": "code",
        "layout": "agentIde",
        "theme": "dark",
        "defaultSize": {"width": 1400, "height": 900},
        "defaultPos": {"x": None, "y": None},
    },
    "blank": {
        "label": "Fenêtre vierge",
        "icon": "window",
        "layout": "blank",
        "theme": "dark",
        "defaultSize": {"width": 1000, "height": 700},
        "defaultPos": {"x": None, "y": None},
    },
    "chatMonitoringDev": {
        "label": "Dev Chat & Monitoring",
        "icon": "code",
        "layout": "chat-monitoring-dev",
        "theme": "dark",
        "defaultSize": {"width": 1700, "height": 1000},
        "defaultPos": {"x": None, "y": None},
    },
    "graphes": {
        "label": "Graphes",
        "icon": "git-branch",
        "layout": "graphes",
        "theme": "dark",
        "defaultSize": {"width": 1600, "height": 900},
        "defaultPos": {"x": None, "y": None},
    },
}

WINDOWS_DIR = mw_home() / "windows"


def _windows_dir() -> Path:
    WINDOWS_DIR.mkdir(parents=True, exist_ok=True)
    return WINDOWS_DIR


def _window_file(window_id: str) -> Path:
    return _windows_dir() / f"{window_id.replace('/', '_')}.json"


def _load_windows() -> list:
    out = []
    for f in sorted(_windows_dir().glob("*.json")):
        try:
            import json
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def op_windows_templates(params):
    """Templates de fenêtres (layout + thème + taille/position par défaut)."""
    return {"status": "ok", "templates": WINDOW_TEMPLATES}


def op_windows_list(params):
    """Profils de fenêtres persistés (layout, thème, position, taille, état)."""
    return {"status": "ok", "windows": _load_windows(), "count": len(_load_windows())}


def op_windows_create(params):
    """Crée un profil de fenêtre (persisté dans ~/.modelweaver/windows/)."""
    import json
    import uuid
    template = params.get("template", "default")
    window_id = params.get("window_id", "")
    if template not in WINDOW_TEMPLATES:
        return {"status": "error", "error": f"template inconnu: {template}"}
    if not window_id:
        window_id = f"win_{uuid.uuid4().hex[:6]}"
    tpl = WINDOW_TEMPLATES[template]
    size = params.get("size") or tpl["defaultSize"]
    pos = params.get("pos") or tpl["defaultPos"]
    profile = {
        "window_id": window_id,
        "template": template,
        "layout": params.get("layout") or tpl["layout"],
        "theme": params.get("theme") or tpl["theme"],
        "title": params.get("title") or tpl["label"],
        "x": pos.get("x"),
        "y": pos.get("y"),
        "width": int(size.get("width", tpl["defaultSize"]["width"])),
        "height": int(size.get("height", tpl["defaultSize"]["height"])),
        "state": params.get("state", "normal"),
        "visible": bool(params.get("visible", True)),
    }
    _window_file(window_id).write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    return {"status": "ok", "window": profile}


def op_windows_update(params):
    """Met à jour un profil de fenêtre (position, taille, état, thème, layout)."""
    import json
    window_id = params.get("window_id", "")
    if not window_id:
        return {"status": "error", "error": "window_id requis"}
    f = _window_file(window_id)
    if not f.exists():
        return {"status": "error", "error": f"fenêtre '{window_id}' introuvable"}
    try:
        profile = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        profile = {"window_id": window_id}
    for key in ("layout", "theme", "title", "state", "visible",
                "x", "y", "width", "height"):
        if key in params:
            profile[key] = params[key]
    f.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    return {"status": "ok", "window": profile}


def op_windows_close(params):
    """Ferme et supprime le profil d'une fenêtre."""
    window_id = params.get("window_id", "")
    if not window_id:
        return {"status": "error", "error": "window_id requis"}
    f = _window_file(window_id)
    if f.exists():
        f.unlink()
    return {"status": "ok", "window_id": window_id, "closed": True}


register("panels/index",     op_panels_index)
register("panels/status",    op_panels_status)
register("panels/file",      op_panels_file)
register("panels/build",     op_panels_build)
register("panels/bundles/list", op_panels_bundles_list)
register("panels/bundles/get",  op_panels_bundles_get)
register("panels/bundles/build", op_panels_bundles_build)
register("windows/templates", op_windows_templates)
register("windows/list",     op_windows_list)
register("windows/create",   op_windows_create)
register("windows/update",   op_windows_update)
register("windows/close",    op_windows_close)
