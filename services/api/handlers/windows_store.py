"""Routes windows-store/* et win-session/* — store de fenêtres structuré en sessions.

Remplace progressivement le modèle windows/ + sessions/ par un store organisé :

    ~/.modelweaver/windows_store/
      session_open.txt            id de la session ACTIVE (ou vide)
      session/<sid>/<sid>.session.yaml   métadonnées de session
      session/<sid>/<wid>.json           fenêtres VIVANTES de la session
      register/<wid>.json                fenêtres ENREGISTRÉES (hors session)

Résolution d'une fenêtre (scope "auto") : live (session active) → registered →
official. Les templates officiels viennent de panels.WINDOW_TEMPLATES.
Toutes les écritures sont atomiques (tmp + os.replace).
"""

import json
import os
import re
import shutil
from pathlib import Path

import yaml

from services._common import mw_home
from services.api.handlers.panels import WINDOW_TEMPLATES
from services.api.router import register

WS_ROOT = mw_home() / "windows_store"
SESSIONS_DIR = WS_ROOT / "session"
REGISTER_DIR = WS_ROOT / "register"
SESSION_OPEN_FILE = WS_ROOT / "session_open.txt"
LAYOUTS_DIR = mw_home() / "layouts"

_SCOPE_LIVE = "live"
_SCOPE_REGISTERED = "registered"
_SCOPE_OFFICIAL = "official"


# ── Primitives ────────────────────────────────────────────

def _ensure_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, content: str) -> None:
    _ensure_dir(path.parent)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_json(path: Path, data: dict) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False))


def _session_dir(sid: str) -> Path:
    return SESSIONS_DIR / sid


def _session_yaml_path(sid: str) -> Path:
    return _session_dir(sid) / f"{sid}.session.yaml"


def _live_file(sid: str, wid: str) -> Path:
    return _session_dir(sid) / f"{wid.replace('/', '_')}.json"


def _registered_file(wid: str) -> Path:
    return REGISTER_DIR / f"{wid.replace('/', '_')}.json"


# ── Session active ────────────────────────────────────────

def _read_session_open():
    if not SESSION_OPEN_FILE.exists():
        return None
    try:
        val = SESSION_OPEN_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return val or None


def _write_session_open(sid) -> None:
    _atomic_write_text(SESSION_OPEN_FILE, (sid or "").strip())


# ── Sessions (YAML) ───────────────────────────────────────

def _read_session(sid: str):
    p = _session_yaml_path(sid)
    if not p.exists():
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("id", sid)
    data.setdefault("name", f"Session {sid}")
    data.setdefault("theme", None)
    data.setdefault("open_windows", [])
    return data


def _write_session(data: dict) -> None:
    sid = data.get("id", "")
    if not sid:
        return
    _ensure_dir(_session_dir(sid))
    _atomic_write_text(_session_yaml_path(sid),
                       yaml.safe_dump(data, allow_unicode=True, sort_keys=False))


def _session_exists(sid: str) -> bool:
    return bool(sid) and _session_dir(sid).exists() and _session_yaml_path(sid).exists()


def _list_sessions() -> list:
    if not SESSIONS_DIR.exists():
        return []
    out = []
    for d in sorted(SESSIONS_DIR.iterdir()):
        if not d.is_dir():
            continue
        s = _read_session(d.name)
        if s:
            out.append(s)
    return out


# ── Fenêtres (profils JSON) ───────────────────────────────

def _load_profiles(dirpath: Path) -> list:
    if not dirpath.exists():
        return []
    out = []
    for f in sorted(dirpath.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def _live_profiles(sid: str) -> list:
    if not sid:
        return []
    return _load_profiles(_session_dir(sid))


def _registered_profiles() -> list:
    return _load_profiles(REGISTER_DIR)


def _official_profile(wid: str) -> dict:
    tpl = WINDOW_TEMPLATES[wid]
    size = tpl.get("defaultSize") or {}
    pos = tpl.get("defaultPos") or {}
    return {
        "window_id": wid,
        "title": tpl.get("label", wid),
        "layout": tpl.get("layout"),
        "theme": tpl.get("theme"),
        "x": pos.get("x"),
        "y": pos.get("y"),
        "width": size.get("width"),
        "height": size.get("height"),
        "state": "normal",
        "source": _SCOPE_OFFICIAL,
    }


def _official_profiles() -> list:
    return [{
        "id": wid,
        "title": tpl.get("label", wid),
        "layout": tpl.get("layout"),
        "theme": tpl.get("theme"),
        "width": (tpl.get("defaultSize") or {}).get("width"),
        "height": (tpl.get("defaultSize") or {}).get("height"),
    } for wid, tpl in WINDOW_TEMPLATES.items()]


def _resolve_profile(wid: str, sid: str, scope: str):
    scope = (scope or "auto").strip().lower() or "auto"
    if scope == "auto":
        if sid and _live_file(sid, wid).exists():
            prof = _resolve_profile(wid, sid, _SCOPE_LIVE)
            if prof:
                return prof
        if _registered_file(wid).exists():
            prof = _resolve_profile(wid, sid, _SCOPE_REGISTERED)
            if prof:
                return prof
        if wid in WINDOW_TEMPLATES:
            return _official_profile(wid)
        return None
    if scope == _SCOPE_LIVE:
        if not sid:
            return None
        p = _live_file(sid, wid)
    elif scope == _SCOPE_REGISTERED:
        p = _registered_file(wid)
    elif scope == _SCOPE_OFFICIAL:
        return _official_profile(wid) if wid in WINDOW_TEMPLATES else None
    else:
        return None
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data["window_id"] = wid
    data["source"] = scope
    return data


def _next_window_id(sid: str) -> str:
    ids = set()
    ids.update(p.get("window_id", "") or "" for p in _live_profiles(sid))
    ids.update(p.get("window_id", "") or "" for p in _registered_profiles())
    if sid and _session_dir(sid).exists():
        ids.update(f.stem for f in _session_dir(sid).glob("*.json"))
    if REGISTER_DIR.exists():
        ids.update(f.stem for f in REGISTER_DIR.glob("*.json"))
    maxn = 0
    for wid in ids:
        m = re.fullmatch(r"window_(\d+)", wid or "")
        if m:
            maxn = max(maxn, int(m.group(1)))
    return f"window_{maxn + 1}"


def _delete_layouts(wid: str) -> list:
    removed = []
    if not LAYOUTS_DIR.exists():
        return removed
    for name in (f"layout-{wid}", wid):
        lp = LAYOUTS_DIR / f"{name}.json"
        if lp.exists():
            lp.unlink()
            removed.append(name)
    return removed


# ── windows-store/ ────────────────────────────────────────

def op_windows_store_state(params):
    """État global du store : session active + toutes les sessions."""
    active = _read_session_open()
    active_session = _read_session(active) if active else None
    return {
        "status": "ok",
        "session_open": active,
        "sessions": _list_sessions(),
        "active_session": active_session,
    }


def op_windows_store_session_active(params):
    """Session active + son contenu (open_windows)."""
    sid = _read_session_open()
    if not sid:
        return {"status": "ok", "session_id": None, "session": None}
    return {"status": "ok", "session_id": sid, "session": _read_session(sid)}


def op_windows_store_session_open(params):
    """Définit la session active (écrit session_open.txt)."""
    sid = (params.get("session_id") or "").strip()
    if not sid:
        return {"status": "error", "error": "session_id requis"}
    if not _session_exists(sid):
        return {"status": "error", "error": f"session inexistante: {sid}"}
    _write_session_open(sid)
    return {"status": "ok", "session_id": sid, "session": _read_session(sid)}


def op_windows_store_windows_list(params):
    """Fenêtres de la session active : live + registered + official."""
    sid = _read_session_open()
    return {
        "status": "ok",
        "live": _live_profiles(sid) if sid else [],
        "registered": _registered_profiles(),
        "official": _official_profiles(),
    }


def op_windows_store_window_get(params):
    """Profil d'une fenêtre (scope auto → live → registered → official)."""
    wid = (params.get("window_id") or "").strip()
    if not wid:
        return {"status": "error", "error": "window_id requis"}
    scope = (params.get("scope") or "auto").strip().lower()
    sid = _read_session_open() if scope in ("live", "auto", "") else None
    prof = _resolve_profile(wid, sid, scope)
    if prof is None:
        return {"status": "error", "error": f"fenêtre '{wid}' introuvable (scope={scope})"}
    return {"status": "ok", "profile": prof}


def op_windows_store_window_save(params):
    """Écrit un profil (scope live → session active ; registered → register/)."""
    wid = (params.get("window_id") or "").strip()
    if not wid:
        return {"status": "error", "error": "window_id requis"}
    scope = (params.get("scope") or "live").strip().lower()
    profile = params.get("profile")
    if not isinstance(profile, dict):
        return {"status": "error", "error": "profile (objet) requis"}
    profile["window_id"] = wid
    if scope == _SCOPE_LIVE:
        sid = _read_session_open()
        if not sid:
            return {"status": "error", "error": "aucune session active"}
        profile["source"] = _SCOPE_LIVE
        _atomic_write_json(_live_file(sid, wid), profile)
        return {"status": "ok", "scope": _SCOPE_LIVE, "session_id": sid, "profile": profile}
    if scope == _SCOPE_REGISTERED:
        profile["source"] = _SCOPE_REGISTERED
        _atomic_write_json(_registered_file(wid), profile)
        return {"status": "ok", "scope": _SCOPE_REGISTERED, "profile": profile}
    return {"status": "error", "error": f"scope invalide: {scope} (attendu live|registered)"}


def op_windows_store_window_close(params):
    """Supprime le .json d'une fenêtre (scope live|registered)."""
    wid = (params.get("window_id") or "").strip()
    if not wid:
        return {"status": "error", "error": "window_id requis"}
    scope = (params.get("scope") or "live").strip().lower()
    if scope == _SCOPE_LIVE:
        sid = _read_session_open()
        if not sid:
            return {"status": "error", "error": "aucune session active"}
        p = _live_file(sid, wid)
        removed = p.exists()
        if removed:
            p.unlink()
        return {"status": "ok", "scope": _SCOPE_LIVE, "session_id": sid,
                "window_id": wid, "closed": removed}
    if scope == _SCOPE_REGISTERED:
        p = _registered_file(wid)
        removed = p.exists()
        if removed:
            p.unlink()
        return {"status": "ok", "scope": _SCOPE_REGISTERED, "window_id": wid,
                "closed": removed}
    return {"status": "error", "error": f"scope invalide: {scope} (attendu live|registered)"}


def op_windows_store_window_register(params):
    """ENREGISTRE une fenêtre (RENOMME) : live → register/, original supprimé.

    Copie le profil vivant (ou les params) vers register/<wid>.json (ou
    register/<new_window_id>.json si renommage), supprime le profil vivant, et
    copie le layout layout-<wid> vers <wid>/<new_window_id> s'il existe.
    """
    wid = (params.get("window_id") or "").strip()
    if not wid:
        return {"status": "error", "error": "window_id requis"}
    new_id = (params.get("new_window_id") or "").strip() or wid
    sid = _read_session_open()
    profile = None
    if sid:
        lp = _live_file(sid, wid)
        if lp.exists():
            try:
                profile = json.loads(lp.read_text(encoding="utf-8"))
            except Exception:
                profile = None
    if profile is None:
        profile = _resolve_profile(wid, sid, "auto")
    if profile is None:
        size = WINDOW_TEMPLATES.get("default", {}).get("defaultSize") or {"width": 1000, "height": 700}
        profile = {
            "window_id": wid,
            "title": params.get("title") or new_id,
            "layout": params.get("layout"),
            "theme": params.get("theme"),
            "x": None,
            "y": None,
            "width": size.get("width"),
            "height": size.get("height"),
            "state": "normal",
        }
    for key in ("title", "theme", "layout"):
        if params.get(key) is not None:
            profile[key] = params[key]
    profile["window_id"] = new_id
    profile["title"] = profile.get("title") or new_id
    profile["source"] = _SCOPE_REGISTERED
    # Écrit sous le NOUVEAU id (renommage). Si un register <new_id> existait,
    # on l'écrase (c'est une reprise volontaire) mais on nettoie l'ancien.
    _atomic_write_json(_registered_file(new_id), profile)
    if sid:
        lp = _live_file(sid, wid)
        if lp.exists():
            lp.unlink()
    if LAYOUTS_DIR.exists():
        src = LAYOUTS_DIR / f"layout-{wid}.json"
        if src.exists():
            _atomic_write_text(LAYOUTS_DIR / f"{new_id}.json", src.read_text(encoding="utf-8"))
            # le layout vivant original est absorbé dans le register
            try:
                (LAYOUTS_DIR / f"layout-{wid}.json").unlink()
            except Exception:
                pass
    return {"status": "ok", "profile": profile, "window_id": new_id}


def op_windows_store_window_unregister(params):
    """Supprime register/<wid>.json et le layout <wid>.json."""
    wid = (params.get("window_id") or "").strip()
    if not wid:
        return {"status": "error", "error": "window_id requis"}
    removed = False
    p = _registered_file(wid)
    if p.exists():
        p.unlink()
        removed = True
    _delete_layouts(wid)
    return {"status": "ok", "window_id": wid, "removed": removed}


def op_windows_store_next_window_id(params):
    """window_N = plus grand numéro connu (live + registered) + 1, sinon window_1."""
    sid = _read_session_open()
    return {"status": "ok", "window_id": _next_window_id(sid)}


def op_windows_store_window_reset(params):
    """Réinitialise une fenêtre : supprime son profil (live/registered) et ses layouts.

    À la prochaine ouverture, la résolution retombe sur registered → official.
    """
    wid = (params.get("window_id") or "").strip()
    if not wid:
        return {"status": "error", "error": "window_id requis"}
    sid = _read_session_open()
    resets = []
    if sid:
        lp = _live_file(sid, wid)
        if lp.exists():
            lp.unlink()
            resets.append(_SCOPE_LIVE)
    rp = _registered_file(wid)
    if rp.exists():
        rp.unlink()
        resets.append(_SCOPE_REGISTERED)
    removed_layouts = _delete_layouts(wid)
    return {"status": "ok", "window_id": wid, "reset": resets or ["none"],
            "layouts_removed": removed_layouts}


# ── win-session/ ──────────────────────────────────────────

def op_win_session_list(params):
    """Liste toutes les sessions + l'id de la session active."""
    return {"status": "ok", "sessions": _list_sessions(), "active": _read_session_open()}


def op_win_session_create(params):
    """Crée une session session_XXXX (numéro croissant). Ne change PAS l'active."""
    _ensure_dir(SESSIONS_DIR)
    maxn = 0
    for d in SESSIONS_DIR.iterdir():
        if d.is_dir():
            m = re.fullmatch(r"session_(\d+)", d.name)
            if m:
                maxn = max(maxn, int(m.group(1)))
    sid = f"session_{maxn + 1:04d}"
    name = (params.get("name") or "").strip() or f"Session {sid}"
    data = {"id": sid, "name": name, "theme": None, "open_windows": []}
    _write_session(data)
    return {"status": "ok", "session": data}


def op_win_session_activate(params):
    """Active une session (écrit session_open.txt)."""
    sid = (params.get("session_id") or "").strip()
    if not sid:
        return {"status": "error", "error": "session_id requis"}
    if not _session_exists(sid):
        return {"status": "error", "error": f"session inexistante: {sid}"}
    _write_session_open(sid)
    return {"status": "ok", "session_id": sid, "session": _read_session(sid)}


def op_win_session_open(params):
    """Active la session ET retourne ses fenêtres ouvertes."""
    sid = (params.get("session_id") or "").strip()
    if not sid:
        return {"status": "error", "error": "session_id requis"}
    if not _session_exists(sid):
        return {"status": "error", "error": f"session inexistante: {sid}"}
    _write_session_open(sid)
    return {"status": "ok", "session_id": sid,
            "open_windows": _read_session(sid).get("open_windows", [])}


def op_win_session_close(params):
    """Déactive la session si elle était active (vide session_open.txt)."""
    sid = (params.get("session_id") or "").strip()
    if _read_session_open() == sid:
        _write_session_open(None)
    return {"status": "ok", "session_id": sid, "active": _read_session_open()}


def op_win_session_rename(params):
    """Renomme une session (id inchangé)."""
    sid = (params.get("session_id") or "").strip()
    name = (params.get("name") or "").strip()
    if not sid:
        return {"status": "error", "error": "session_id requis"}
    if not name:
        return {"status": "error", "error": "name requis"}
    if not _session_exists(sid):
        return {"status": "error", "error": f"session inexistante: {sid}"}
    data = _read_session(sid)
    data["name"] = name
    _write_session(data)
    return {"status": "ok", "session": data}


def op_win_session_set_theme(params):
    """Met à jour le thème d'une session."""
    sid = (params.get("session_id") or "").strip()
    if not sid:
        return {"status": "error", "error": "session_id requis"}
    if not _session_exists(sid):
        return {"status": "error", "error": f"session inexistante: {sid}"}
    data = _read_session(sid)
    data["theme"] = params.get("theme")
    _write_session(data)
    return {"status": "ok", "session": data}


def op_win_session_add_window(params):
    """Ajoute window_id à open_windows (si absent)."""
    sid = (params.get("session_id") or "").strip()
    wid = (params.get("window_id") or "").strip()
    if not sid:
        return {"status": "error", "error": "session_id requis"}
    if not wid:
        return {"status": "error", "error": "window_id requis"}
    if not _session_exists(sid):
        return {"status": "error", "error": f"session inexistante: {sid}"}
    data = _read_session(sid)
    if wid not in data.get("open_windows", []):
        data.setdefault("open_windows", []).append(wid)
        _write_session(data)
    return {"status": "ok", "session_id": sid, "open_windows": data.get("open_windows", [])}


def op_win_session_remove_window(params):
    """Retire window_id de open_windows."""
    sid = (params.get("session_id") or "").strip()
    wid = (params.get("window_id") or "").strip()
    if not sid:
        return {"status": "error", "error": "session_id requis"}
    if not wid:
        return {"status": "error", "error": "window_id requis"}
    if not _session_exists(sid):
        return {"status": "error", "error": f"session inexistante: {sid}"}
    data = _read_session(sid)
    if wid in data.get("open_windows", []):
        data["open_windows"].remove(wid)
        _write_session(data)
    return {"status": "ok", "session_id": sid, "open_windows": data.get("open_windows", [])}


def op_win_session_delete(params):
    """Supprime le dossier de la session (et déactive si c'était l'active)."""
    sid = (params.get("session_id") or "").strip()
    if not sid:
        return {"status": "error", "error": "session_id requis"}
    d = _session_dir(sid)
    if not d.exists():
        return {"status": "error", "error": f"session inexistante: {sid}"}
    if _read_session_open() == sid:
        _write_session_open(None)
    shutil.rmtree(d, ignore_errors=True)
    return {"status": "ok", "session_id": sid, "deleted": True}


def op_win_session_open_windows(params):
    """Retourne les ids des fenêtres ouvertes d'une session."""
    sid = (params.get("session_id") or "").strip()
    if not sid:
        return {"status": "error", "error": "session_id requis"}
    if not _session_exists(sid):
        return {"status": "error", "error": f"session inexistante: {sid}"}
    return {"status": "ok", "session_id": sid,
            "open_windows": _read_session(sid).get("open_windows", [])}


# ── Route registration ────────────────────────────────────

register("windows-store/state",                op_windows_store_state)
register("windows-store/session-active",       op_windows_store_session_active)
register("windows-store/session-open",         op_windows_store_session_open)
register("windows-store/windows-list",         op_windows_store_windows_list)
register("windows-store/window-get",           op_windows_store_window_get)
register("windows-store/window-save",          op_windows_store_window_save)
register("windows-store/window-close",         op_windows_store_window_close)
register("windows-store/window-register",      op_windows_store_window_register)
register("windows-store/window-unregister",    op_windows_store_window_unregister)
register("windows-store/next-window-id",       op_windows_store_next_window_id)
register("windows-store/window-reset",         op_windows_store_window_reset)

register("win-session/list",                   op_win_session_list)
register("win-session/create",                 op_win_session_create)
register("win-session/activate",               op_win_session_activate)
register("win-session/open",                   op_win_session_open)
register("win-session/close",                  op_win_session_close)
register("win-session/rename",                 op_win_session_rename)
register("win-session/set-theme",              op_win_session_set_theme)
register("win-session/add-window",             op_win_session_add_window)
register("win-session/remove-window",          op_win_session_remove_window)
register("win-session/delete",                 op_win_session_delete)
register("win-session/open-windows",           op_win_session_open_windows)
