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


# ── Fenêtres (templates de base) ─────────────────────────────────────

WINDOW_TEMPLATES = {
    "dashboard": {
        "label": "Dashboard",
        "icon": "dashboard",
        "layout": "dashboard",
        "defaultSize": {"width": 1200, "height": 800},
    },
    "default": {
        "label": "Par défaut",
        "icon": "window",
        "layout": "default",
        "defaultSize": {"width": 1000, "height": 700},
    },
    "agentIde": {
        "label": "Agent IDE",
        "icon": "code",
        "layout": "agentIde",
        "defaultSize": {"width": 1400, "height": 900},
    },
}


def op_windows_templates(params):
    """Templates de fenêtres de base."""
    return {"status": "ok", "templates": WINDOW_TEMPLATES}


def op_windows_list(params):
    """Fenêtres ouvertes (déclarées par la GUI au runtime)."""
    try:
        from modules.sql.runtime_repo import RuntimeDB
        db = RuntimeDB()
        db.conn.execute("""CREATE TABLE IF NOT EXISTS gui_windows (
            window_id TEXT PRIMARY KEY, template TEXT, title TEXT,
            opened_at TEXT DEFAULT (datetime('now')))""")
        rows = db.conn.execute("SELECT * FROM gui_windows").fetchall()
        db.close()
        return {"status": "ok", "windows": [dict(r) for r in rows]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_windows_create(params):
    """Ouvre une fenêtre (template de base)."""
    template = params.get("template", "default")
    window_id = params.get("window_id", "")
    if template not in WINDOW_TEMPLATES:
        return {"status": "error", "error": f"template inconnu: {template}"}
    if not window_id:
        import uuid
        window_id = f"win_{uuid.uuid4().hex[:6]}"
    try:
        from modules.sql.runtime_repo import RuntimeDB
        db = RuntimeDB()
        db.conn.execute("""CREATE TABLE IF NOT EXISTS gui_windows (
            window_id TEXT PRIMARY KEY, template TEXT, title TEXT,
            opened_at TEXT DEFAULT (datetime('now')))""")
        db.conn.execute(
            "INSERT OR REPLACE INTO gui_windows (window_id, template, title) "
            "VALUES (?, ?, ?)",
            (window_id, template, WINDOW_TEMPLATES[template]["label"]))
        db.conn.commit()
        db.close()
        return {"status": "ok", "window_id": window_id,
                "template": template, "layout": WINDOW_TEMPLATES[template]["layout"]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_windows_close(params):
    """Ferme une fenêtre."""
    window_id = params.get("window_id", "")
    if not window_id:
        return {"status": "error", "error": "window_id requis"}
    try:
        from modules.sql.runtime_repo import RuntimeDB
        db = RuntimeDB()
        db.conn.execute("""CREATE TABLE IF NOT EXISTS gui_windows (
            window_id TEXT PRIMARY KEY, template TEXT, title TEXT,
            opened_at TEXT DEFAULT (datetime('now')))""")
        db.conn.execute("DELETE FROM gui_windows WHERE window_id = ?", (window_id,))
        db.conn.commit()
        db.close()
        return {"status": "ok", "window_id": window_id, "closed": True}
    except Exception as e:
        return {"status": "error", "error": str(e)}


register("panels/index",     op_panels_index)
register("panels/status",    op_panels_status)
register("panels/file",      op_panels_file)
register("windows/templates", op_windows_templates)
register("windows/list",     op_windows_list)
register("windows/create",   op_windows_create)
register("windows/close",    op_windows_close)
