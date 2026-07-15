#!/usr/bin/env python3
"""
ModelWeaver — Daemon API locale.

Backend unique, indépendant de toute GUI. Expose sur 127.0.0.1 (HTTP/JSON) les
opérations « accessibles depuis l'externe » définies dans ARCHITECTURE_API.md.
Toute interface (GUI Tauri v1/v2, CLI, web, TUI) est un simple client.

Sécurité :
  - bind STRICT sur 127.0.0.1 (jamais 0.0.0.0)
  - token de session écrit dans ~/.modelweaver/api.token (perms 600), exigé via
    l'en-tête `Authorization: Bearer <token>` pour toute route /v1/*.

Découverte par les clients :
  - ~/.modelweaver/api.port  : port courant
  - ~/.modelweaver/api.token : token de session

Usage:
  python mw_daemon.py [--port 8770]
"""
import sys
import os
import json
import time
import secrets
import argparse
import platform
import contextlib
import sys
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# Ancrage du dépôt sur sys.path (modules/, services/, sql/ à la racine) AVANT
# tout import de services.* — indispensable quand le daemon est lancé directement
# (ex. supervisé par Rust : `python services/api/daemon.py serve`).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services._common import mw_home
from modules.system.deps import install_system_package, install_target_dependencies

# Le daemon est le backend unique et indépendant de toute GUI. Il consomme
# directement les modules (source de vérité) et le service installer_worker
# (file de jobs + install/uninstall). Aucune dépendance à gui_helper.
from services.installer_worker import jobs
from services.watch_sysstate import service as sysstate
from modules.sql.db import ModelWeaverDB, CatalogueDB, RuntimeDB, AgentsDB, read_db_version, fetch_remote_to_local
from modules.checker.checker import Checker
from services._common import _db_paths, _quiet_stdout, log_to_file, runtime_db_path
from modules.llm_manager.litellm_bridge import LiteLLMBridge
from modules.llm_manager.local_engines import get_local_engine_manager
from modules.llm_manager.base_bridge import BridgeError, ErrorCategory
from AgentFrameWork.router import (
    capabilities_catalog as router_capabilities,
)

API_VERSION = "v1"
MW_VERSION = "0.6.23.0"


def _mw_dir() -> Path:
    d = mw_home()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Opérations « métier » : implémentées ici en consommant modules/services ──

def op_system_info(_params):
    return {
        "os": sys.platform,
        "system": platform.system(),
        "arch": platform.machine(),
        "home": str(Path.home()),
        "python": platform.python_version(),
    }


def check_python_deps():
    """Vérifie les dépendances pip requises (extrait de l'ancien gui_helper)."""
    import subprocess
    import json as _json
    required = [
        {"name": "litellm", "module": "litellm", "type": "pip"},
        {"name": "open-webui", "module": "open_webui", "type": "pip"},
        {"name": "keyring", "module": "keyring", "type": "pip"},
        {"name": "cryptography", "module": "cryptography", "type": "pip"},
        {"name": "requests", "module": "requests", "type": "pip"},
        {"name": "psutil", "module": "psutil", "type": "pip"},
    ]
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json"],
                           capture_output=True, text=True, timeout=15)
        installed = {p["name"].lower().replace("-", "_"): p["version"]
                     for p in _json.loads(r.stdout)} if r.returncode == 0 else {}
    except Exception:
        installed = {}
    for dep in required:
        key = dep["module"].lower().replace("-", "_")
        dep["installed"] = key in installed
        dep["version"] = installed.get(key)
        dep["min_version"] = {"litellm": "1.0", "open-webui": "0.1", "keyring": "23.0",
                              "cryptography": "35.0", "requests": "2.0", "psutil": "5.0"}.get(dep["name"])
    return {"deps": required}


def init_databases():
    jobs.ensure_install_jobs()
    mw = _get_mw()
    mw._ensure_schema()
    mw.commit()
    cat = _get_cat()
    cat._ensure_schema()
    cat.conn.commit()
    return {"status": "ok", "mw_db": str(mw.db_path), "cat_db": str(cat.db_path)}


def check_databases():
    mw_path, cat_path = _db_paths()
    result = {
        "modelweaver_db": {"path": str(mw_path), "exists": mw_path.exists()},
        "catalogue_db": {"path": str(cat_path), "exists": cat_path.exists()},
    }
    if result["modelweaver_db"]["exists"]:
        try:
            result["modelweaver_db"]["tool_count"] = len(_get_mw().tools.list_all())
        except Exception as e:
            result["modelweaver_db"]["error"] = str(e)
    if result["catalogue_db"]["exists"]:
        try:
            cur = _get_cat().conn.execute("SELECT COUNT(*) FROM catalogue_providers")
            result["catalogue_db"]["provider_count"] = cur.fetchone()[0]
        except Exception as e:
            result["catalogue_db"]["error"] = str(e)
    return result


def seed_recipes(cat):
    """Backfill catalogue_outils/versions/recettes + outils_popularite depuis
    tools.json et les .mw.yaml shippés. Idempotent (INSERT OR IGNORE / dédupe)."""
    from modules.sql.db import (_ensure_classes_outils_table, resolve_classe_id,
                                 _default_class_for_ref)
    data_path = str(_REPO_ROOT / "modules" / "catalogue" / "data" / "tools.json")
    try:
        with open(data_path) as f:
            tools = json.load(f)
    except Exception:
        tools = []
    if isinstance(tools, dict):
        tools = tools.get("tools", [])
    # S'assurer que la taxonomie classes_outils existe + seed
    try:
        _ensure_classes_outils_table(cat.conn)
    except Exception:
        pass
    for t in tools:
        ref = t.get("ref")
        if not ref:
            continue
        # classe métier : champ 'classe' du JSON, sinon déduite du ref
        classe_ref = t.get("classe") or _default_class_for_ref(ref)
        try:
            classe_id = resolve_classe_id(cat.conn, classe_ref)
        except Exception:
            classe_id = None
        cat.conn.execute(
            "INSERT OR IGNORE INTO catalogue_outils (ref, nom, description, tool_type, classe_outil_id) VALUES (?,?,?,?,?)",
            (ref, t.get("name", ref), t.get("description", ""), t.get("tool_type"), classe_id))
    cat.conn.commit()
    recipe_dir = _REPO_ROOT / "modules" / "installer" / "install_recipe"
    if not recipe_dir.exists():
        return
    for global_file in recipe_dir.rglob("global.yaml"):
        ref = global_file.parent.name
        if ref.endswith(".mw"):
            ref = ref[:-3]
        row = cat.conn.execute("SELECT outil_id FROM catalogue_outils WHERE ref=?", (ref,)).fetchone()
        if not row:
            continue
        outil_id = row["outil_id"]
        cat.conn.execute(
            "INSERT OR IGNORE INTO catalogue_versions (outil_id, nom_version) VALUES (?, 'latest')",
            (outil_id,))
        vid = cat.conn.execute(
            "SELECT version_id FROM catalogue_versions WHERE outil_id=? AND nom_version='latest'",
            (outil_id,)).fetchone()["version_id"]
        for mgr_file in global_file.parent.rglob("*.yaml"):
            if mgr_file.name == "global.yaml":
                continue
            parts = mgr_file.relative_to(global_file.parent).parts
            if len(parts) != 3:
                continue
            os_k, arch_k, mgr = parts[0], parts[1], parts[2][:-5]
            exists = cat.conn.execute(
                "SELECT 1 FROM catalogue_recettes WHERE version_id=? AND os=? AND arch=? AND manager=?",
                (vid, os_k, arch_k, mgr)).fetchone()
            if exists:
                continue
            content = mgr_file.read_text()
            cat.conn.execute(
                """INSERT INTO catalogue_recettes
                   (version_id, os, arch, manager, package, content, confidence, createur_id)
                   VALUES (?,?,?,?,?,?,1.0,'system')""",
                (vid, os_k, arch_k, mgr, ref, content))
        cat.conn.execute("INSERT OR IGNORE INTO outils_popularite (outil_id) VALUES (?)", (outil_id,))
    cat.conn.commit()


def seed_catalogue():
    cat = _get_cat()
    # Seed models + provider_models (idempotent, indépendant des outils).
    # Ainsi le catalogue LLM est toujours peuplé même si les outils
    # ont déjà été seedés (cas d'un catalogue pré-existant).
    from modules.llm_manager.llm_manager import seed_providers, seed_models, seed_provider_models
    count_providers = seed_providers(cat)
    count_models = seed_models(cat)
    count_pm = seed_provider_models(cat)
    cat.conn.commit()
    cur = cat.conn.execute("SELECT COUNT(*) FROM catalogue_outils")
    if cur.fetchone()[0] > 0:
        try:
            seed_recipes(cat)
        except Exception:
            pass
        return {"status": "ok", "seeded": False, "note": "catalogue already populated (models synced)"}
    # Tools (seulement si vides)
    data_path = str(_REPO_ROOT / "modules" / "catalogue" / "data" / "tools.json")
    with open(data_path) as f:
        rows = json.load(f)
    count_tools = cat.sync_tools(rows)
    cat.conn.commit()
    try:
        seed_recipes(cat)
    except Exception as e:
        from services.logger import MWLogger; MWLogger("daemon").warning("seed_recipes échoué", error=str(e))
    try:
        _get_rt().bump_meta("catalogue")
    except Exception:
        pass
    return {
        "status": "ok", "seeded": True,
        "tools": count_tools, "providers": count_providers,
        "models": count_models, "provider_models": count_pm,
    }


def get_catalogue_tools():
    import platform
    cat = _get_cat()
    os_key = platform.system().lower()
    arch = platform.machine().lower()
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(arch, arch)
    return cat.get_catalogue_tools(os_key=os_key, arch_key=arch)


def get_installed_tools():
    mw = _get_mw()
    rows = mw.local_tools.list_all()
    out = [{
        "ref": r.get("outil_ref"),
        "name": r.get("nom"),
        "version": r.get("version_installee") or r.get("nom_version"),
        "status": r.get("status"),
        "install_path": r.get("install_path"),
        "classe": r.get("classe_nom"), "classe_ref": r.get("classe_ref"),
    } for r in rows]
    return {"tools": out, "count": len(out)}


def save_system_state():
    mw = _get_mw()
    Checker().update_local_db(mw)
    mw.commit()
    return {"status": "ok"}


def sync_catalogue_remote(url=None):
    if not url:
        url = os.environ.get("MODELWEAVER_CATALOGUE_URL", "http://localhost:8765/api")
    cat = _get_cat()
    if cat.conn.execute("SELECT COUNT(*) FROM catalogue_outils").fetchone()[0] == 0:
        with _quiet_stdout():
            seed_catalogue()
    with _quiet_stdout():
        results = cat.sync_from_url(url)
    try:
        _get_rt().bump_meta("catalogue")
    except Exception:
        pass
    return {"status": "ok", "url": url, "results": results}


def update_tools_table():
    mw = _get_mw()
    count = mw.scan_installed_tools()
    return {"status": "ok", "updated": count}


def op_tools_install_all(_params):
    """Queue tous les outils du catalogue non encore installés."""
    from modules.checker.checker import Checker
    cat = _get_cat()
    cur = cat.conn.execute("SELECT ref, nom AS name FROM catalogue_outils")
    all_tools = cur.fetchall()
    mw = _get_mw()
    installed = {t.get("tool_ref") or t.get("ref") for t in mw.local_tools.list_all()}
    jobs.ensure_install_jobs()
    queued = []
    for ref, name in all_tools:
        if ref not in installed:
            jid = jobs.enqueue_job(ref, "install")
            queued.append({"ref": ref, "name": name, "job_id": jid})
    return {"status": "ok", "queued": len(queued), "tools": queued}


import threading
_DB_LOCK = threading.Lock()
_MW_INSTANCE = None
_CAT_INSTANCE = None
_KM_INSTANCE = None
_LLM_INSTANCE = None

def _get_mw():
    global _MW_INSTANCE
    if _MW_INSTANCE is None:
        with _DB_LOCK:
            if _MW_INSTANCE is None:
                mw_path, _ = _db_paths()
                _MW_INSTANCE = ModelWeaverDB(mw_path)
    return _MW_INSTANCE

def _get_cat():
    global _CAT_INSTANCE
    if _CAT_INSTANCE is None:
        with _DB_LOCK:
            if _CAT_INSTANCE is None:
                _, cat_path = _db_paths()
                _CAT_INSTANCE = CatalogueDB(cat_path)
    return _CAT_INSTANCE


_RT_INSTANCE = None


def _get_rt():
    global _RT_INSTANCE
    if _RT_INSTANCE is None:
        with _DB_LOCK:
            if _RT_INSTANCE is None:
                _RT_INSTANCE = RuntimeDB(runtime_db_path())
    return _RT_INSTANCE

def _get_km():
    from modules.key_manager.key_manager import KeyManager
    global _KM_INSTANCE
    if _KM_INSTANCE is None:
        _KM_INSTANCE = KeyManager(db=_get_mw())
    return _KM_INSTANCE

def _get_llm():
    from modules.llm_manager.llm_manager import LLMManager
    global _LLM_INSTANCE
    if _LLM_INSTANCE is None:
        _LLM_INSTANCE = LLMManager(cat=_get_cat())
    return _LLM_INSTANCE


_BRIDGE_INSTANCE = None

def _get_bridge():
    global _BRIDGE_INSTANCE
    if _BRIDGE_INSTANCE is None:
        _BRIDGE_INSTANCE = LiteLLMBridge(cat=_get_cat(), km=_get_km())
    return _BRIDGE_INSTANCE

def _process_install_jobs():
    """Process one queued install job (blocking). Called by the background thread."""
    rt = _get_rt()
    # Phase 1: pick a job and mark running (atomically under lock)
    _DB_LOCK.acquire()
    try:
        cur = rt.conn.execute(
            "SELECT id, ref, name, job_type FROM install_jobs WHERE status='queued' ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if not row:
            return
        jid, ref, name, job_type = row
        rt.conn.execute("UPDATE install_jobs SET status='running', updated_at=strftime('%s','now') WHERE id=?",
                    (jid,))
        rt.conn.commit()
    finally:
        _DB_LOCK.release()
    cat_singleton = _get_cat()
    mw = _get_mw()
    # Phase 2: run the install (no lock — may take minutes)
    try:
        if job_type == "install":
            result = jobs.install_tool(ref, mw_shared=mw, cat_shared=cat_singleton)
        elif job_type == "uninstall":
            result = jobs.uninstall_tool(ref, mw_shared=mw, cat_shared=cat_singleton)
        else:
            result = {"status": "error", "error": f"unknown job_type: {job_type}"}
        status = "installed" if result.get("status") == "ok" else "failed"
        log = json.dumps(result)
    except Exception as e:
        result = {"status": "error", "error": str(e)}
        status = "failed"
        log = str(e)[:500]
    # Phase 3: update job status (brief lock)
    _DB_LOCK.acquire()
    try:
        rt.conn.execute("UPDATE install_jobs SET status=?, log=?, updated_at=strftime('%s','now') WHERE id=?",
                    (status, log[:500], jid))
        rt.conn.commit()
    finally:
        _DB_LOCK.release()


def _job_processor_loop(interval: float = 5.0):
    """Background thread : consume la queue install_jobs."""
    def _loop():
        while True:
            try:
                _process_install_jobs()
            except Exception:
                pass
            time.sleep(interval)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def op_jobs_list(_params):
    jobs.ensure_install_jobs()
    return jobs.list_jobs()


def op_jobs_add(params):
    ref = params.get("ref")
    job_type = params.get("job_type", "install")
    if not ref:
        return {"status": "error", "error": "missing 'ref'"}
    jobs.ensure_install_jobs()
    jid = jobs.enqueue_job(ref, job_type)
    return {"status": "ok", "job_id": jid, "duplicate": jid == 0}


def op_jobs_status(params):
    jid = params.get("id")
    if jid is None:
        return {"status": "error", "error": "missing 'id'"}
    jobs.ensure_install_jobs()
    st, log = jobs.job_status(int(jid))
    return {"status": "ok", "job_status": st, "log": log}


def op_jobs_cancel(params):
    jid = params.get("id")
    if jid is None:
        return {"status": "error", "error": "missing 'id'"}
    jobs.ensure_install_jobs()
    jobs.cancel_job(int(jid))
    return {"status": "ok"}


def op_jobs_clear(_params):
    jobs.ensure_install_jobs()
    jobs.clear_jobs()
    return {"status": "ok"}


def op_logs_read(_params):
    log_file = _mw_dir() / "logs" / "installer.log"
    try:
        return {"status": "ok", "log": log_file.read_text(encoding="utf-8", errors="replace")}
    except Exception as e:
        return {"status": "ok", "log": "", "note": str(e)}


def op_logs_write(params):
    log_to_file(params.get("level", "INFO"), params.get("message", ""))
    return {"status": "ok"}


def _op_tarif_info(_params):
    from services.tarif import tarif_info
    return tarif_info()


def _op_tarif_sync(url=None):
    from services.tarif import sync_tarif
    return sync_tarif(url)


def op_deps_check(_params):
    from services.depends import check_all_units
    return check_all_units(_REPO_ROOT)


def _rescan_local_tools():
    """Re-détecte les outils installés (ex. après install de dépendances comme
    keyring) et peuple `local_tools`. Bump implicite de la DB inventory."""
    try:
        mw = _get_mw()
        mw.scan_installed_tools()
        mw.commit()
    except Exception as e:
        from services.logger import MWLogger; MWLogger("daemon").warning("rescan outils installés échoué", error=str(e))


def op_deps_install(params):
    package = params.get("package")
    if not package:
        return {"status": "error", "error": "missing 'package'"}
    res = install_system_package(package)
    if res.get("status") == "ok":
        try:
            _get_rt().bump_meta("dependencies")
        except Exception:
            pass
        _rescan_local_tools()
    return res


def op_deps_install_target(params):
    """Installe les dépendances requises de la cible via le script compilé.

    target vide -> auto-détecté. Script absent -> erreur 'fichier <script> absent'.
    """
    target = params.get("target", "") or ""
    include_optional = bool(params.get("include_optional", False))
    res = install_target_dependencies(target=target, include_optional=include_optional)
    # Signal à la GUI (pseudo-domaine 'dependencies') que l'état a changé.
    if res.get("status") == "ok":
        try:
            _get_rt().bump_meta("dependencies")
        except Exception:
            pass
        _rescan_local_tools()
    return res


def op_db_versions(params):
    """Renvoie les `PRAGMA data_version` par DB (+ meta 'dependencies').

    La GUI poll ce endpoint à 20 Hz et ne rafraîchit que les panneaux du
    domaine dont la DB a changé (split physique : catalogue / inventory /
    runtime / dependencies). Pas de triggers par table.
    """
    out = {}
    try:
        out["inventory"] = read_db_version(_get_mw().conn)
    except Exception:
        out["inventory"] = 0
    try:
        # combine data_version (écritures externes) + meta (écritures du daemon lui-même)
        out["catalogue"] = max(read_db_version(_get_cat().conn), _get_rt().read_meta("catalogue"))
    except Exception:
        out["catalogue"] = 0
    try:
        out["runtime"] = read_db_version(_get_rt().conn)
    except Exception:
        out["runtime"] = 0
    try:
        out["dependencies"] = _get_rt().read_meta("dependencies")
    except Exception:
        out["dependencies"] = 0
    return out


def op_deps_check_manifest(params):
    """Liste les dépendances du manifeste pour la cible, avec statut installé.

    Utilise le paquet DISPONIBLE pour la cible (targets.<target>) et vérifie
    selon le langage (system -> dpkg, python -> pip show).
    """
    from modules.system import deps as deps_mod
    target = params.get("target", "") or ""
    try:
        if not target:
            target = deps_mod.detect_target()
        if not target:
            return {"status": "error", "error": "cible non détectée"}
        m = deps_mod.load_manifest()
        out = []
        for dep in m.get("dependencies", []):
            pkg = dep.get("targets", {}).get(target)
            if not pkg:
                continue  # pas de paquet disponible pour cette cible
            installed = deps_mod.is_dependency_installed(dep.get("language", "system"), pkg)
            required = (not dep.get("optional")) and dep.get("safe") and dep.get("weight") == "light"
            out.append({
                "name": dep["name"],
                "description": dep.get("description", ""),
                "language": dep.get("language", "system"),
                "safe": dep.get("safe"),
                "weight": dep.get("weight"),
                "optional": dep.get("optional", False),
                "required": required,
                "target_pkg": pkg,
                "installed": installed,
            })
        return {"target": target, "dependencies": out}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Key Manager ────────────────────────────────────────────────

def op_keys_set(params):
    provider_ref = params.get("provider_ref")
    api_key = params.get("api_key")
    if not provider_ref or not api_key:
        return {"status": "error", "error": "missing 'provider_ref' or 'api_key'"}
    km = _get_km()
    ref = km.set_key(
        provider_ref=provider_ref,
        api_key=api_key,
        api_base=params.get("api_base"),
        identity=params.get("identity", "default"),
        tag=params.get("tag", "paid"),
        grade=params.get("grade"),
    )
    from services.audit import audit
    audit("keys.set", provider_ref=provider_ref, ref=ref, ok=True)
    return {"status": "ok", "ref": ref}


def op_keys_get(params):
    km = _get_km()
    try:
        key = km.get_key(
            provider_ref=params["provider_ref"],
            identity=params.get("identity", "default"),
        )
    except Exception as e:
        from modules.key_manager.key_manager import KeyLockedError
        if isinstance(e, KeyLockedError):
            return {"status": "locked"}
        raise
    if not key:
        return {"status": "not_found"}
    return {"status": "ok", "key": key}


def op_keys_set_lock(params):
    ref = params.get("ref")
    if not ref:
        return {"status": "error", "error": "missing 'ref'"}
    locked = bool(params.get("locked", True))
    km = _get_km()
    ok = km.set_lock(ref, locked)
    from services.audit import audit
    audit("keys.set_lock", ref=ref, locked=locked, ok=ok)
    return {"status": "ok" if ok else "error", "ref": ref, "locked": locked}


def op_keys_list(_params):
    km = _get_km()
    safe = km.list_keys()  # déjà sans key_value
    for k in safe:
        if not k.get("key_display"):
            k["key_display"] = "****"
    return {"keys": safe, "count": len(safe)}


def op_keys_delete(params):
    km = _get_km()
    ref = params.get("ref")
    provider_ref = params.get("provider_ref")
    from services.audit import audit
    if ref:
        ok = km.delete_key(ref)
        audit("keys.delete", ref=ref, ok=ok)
        return {"status": "ok" if ok else "error", "deleted": ok}
    if provider_ref:
        keys = km.list_keys()
        deleted = 0
        for k in keys:
            if k.get("provider_ref") == provider_ref:
                if km.delete_key(k["ref"]):
                    deleted += 1
        audit("keys.delete", provider_ref=provider_ref, deleted=deleted, ok=True)
        return {"status": "ok", "deleted": deleted}
    return {"status": "error", "error": "missing 'ref' or 'provider_ref'"}


def op_keys_onboard(params):
    from modules.key_manager.onboarder import Onboarder
    km = _get_km()
    onboarder = Onboarder(km)
    env_path = params.get("env_path", str(_REPO_ROOT / ".env"))
    count = onboarder.onboard_from_env(Path(env_path))
    from services.audit import audit
    audit("keys.onboard", env_path=env_path, imported=count, ok=count > 0)
    return {"status": "ok", "imported": count}


def op_providers_list(_params):
    from modules.sql.db import CatalogueDB
    cat = _get_cat()
    cur = cat.conn.execute(
        "SELECT ref, name, provider_type, api_type, website, is_free_tier_provider "
        "FROM catalogue_providers ORDER BY name")
    cols = [d[0] for d in cur.description]
    providers = [dict(zip(cols, row)) for row in cur.fetchall()]
    # Joindre les endpoints possédés par le provider (table provider_endpoints)
    try:
        ecur = cat.conn.execute(
            "SELECT p.ref, e.endpoint_id, e.label, e.endpoint_url, e.api_type, e.is_default "
            "FROM provider_endpoints e JOIN catalogue_providers p ON p.id = e.provider_id")
        eps: dict = {}
        for prow in ecur.fetchall():
            eps.setdefault(prow[0], []).append(
                {"id": prow[1], "label": prow[2], "endpoint_url": prow[3],
                 "api_type": prow[4], "is_default": bool(prow[5])})
        for p in providers:
            p["endpoints"] = eps.get(p["ref"], [])
    except Exception:
        for p in providers:
            p["endpoints"] = []
    # Enrichir avec has_key : true si une clé API est fournie pour ce provider
    try:
        km = _get_km()
        has_key_refs = set(km.list_providers())
        for p in providers:
            p["has_key"] = p["ref"] in has_key_refs
    except Exception:
        for p in providers:
            p["has_key"] = False
    return {"providers": providers, "count": len(providers)}


# ── LLM Manager ───────────────────────────────────────────────

def op_llm_models_list(params):
    llm = _get_llm()
    # Restriction : ne retourner les modèles que pour les providers
    # qui ont une clé API fournie (sauf ollama/builtin/local qui n'en ont pas besoin).
    try:
        km = _get_km()
        has_key_refs = set(km.list_providers())
    except Exception:
        has_key_refs = set()
    # Providers qui ne nécessitent pas de clé (ollama, builtin, local)
    cat = _get_cat()
    no_key_refs = set()
    cur = cat.conn.execute(
        "SELECT ref FROM catalogue_providers WHERE provider_type IN ('ollama', 'builtin', 'local')")
    no_key_refs = {row[0] for row in cur.fetchall()}
    allowed_refs = has_key_refs | no_key_refs

    provider_ref = params.get("provider_ref")
    if provider_ref:
        if provider_ref not in allowed_refs:
            return {"models": [], "count": 0, "error": "no_api_key",
                    "message": f"Aucune clé API fournie pour le provider '{provider_ref}'"}
        models = llm.list_models(provider_ref=provider_ref)
    else:
        # Tous les modèles, mais filtrés par provider (seulement ceux avec clé ou sans-clé-requis)
        all_models = llm.list_models()
        models = [m for m in all_models if m.get("provider_ref") in allowed_refs]
    return {"models": models, "count": len(models)}


def op_llm_recommend(params):
    llm = _get_llm()
    use_case = params.get("use_case", "chat")
    technical_level = params.get("technical_level", "free")
    valid_use_cases = ("chat", "coding", "analysis", "writing")
    valid_levels = ("free", "paid", "local")
    if use_case not in valid_use_cases:
        return {"status": "error", "error": f"use_case must be one of {valid_use_cases}"}
    if technical_level not in valid_levels:
        return {"status": "error", "error": f"technical_level must be one of {valid_levels}"}
    result = llm.recommend(use_case=use_case, technical_level=technical_level)
    # Filtrer : ne garder que les reco dont le provider a une clé (ou provider sans-clé-requis)
    try:
        km = _get_km()
        has_key_refs = set(km.list_providers())
    except Exception:
        has_key_refs = set()
    cat = _get_cat()
    cur = cat.conn.execute(
        "SELECT ref FROM catalogue_providers WHERE provider_type IN ('ollama', 'builtin', 'local')")
    no_key_refs = {row[0] for row in cur.fetchall()}
    allowed_refs = has_key_refs | no_key_refs
    filt = [r for r in result.get("recommendations", [])
            if r.get("provider") in allowed_refs]
    result["recommendations"] = filt
    result["count"] = len(filt)
    return result


# ── LLM Bridge ─────────────────────────────────────────────────

def op_llm_chat(params):
    """Chat avec un modèle via le bridge. params: provider_ref, model_ref, messages[, temperature, max_tokens, system_prompt]"""
    bridge = _get_bridge()
    provider_ref = params.get("provider_ref")
    model_ref = params.get("model_ref")
    messages = params.get("messages", [])
    if not provider_ref or not model_ref or not messages:
        return {"status": "error", "error": "provider_ref, model_ref et messages requis"}
    try:
        resp = bridge.chat(
            provider_ref=provider_ref,
            model_ref=model_ref,
            messages=messages,
            temperature=params.get("temperature", 0.7),
            max_tokens=params.get("max_tokens"),
            system_prompt=params.get("system_prompt"),
            stream=False,
        )
        tokens = 0
        if resp.usage:
            tokens = (resp.usage.get("prompt_tokens", 0) or 0) + (resp.usage.get("completion_tokens", 0) or 0)
        if tokens:
            from services.ratelimit import check_rate_limit
            try:
                check_rate_limit("llm/chat", "127.0.0.1", tokens=tokens)
            except Exception:
                pass
        return {
            "status": "ok",
            "content": resp.content,
            "model": resp.model,
            "finish_reason": resp.finish_reason,
            "usage": resp.usage,
        }
    except BridgeError as be:
        return {
            "status": "error",
            "error": be.message,
            "category": be.category.value,
            "provider_ref": be.provider_ref,
            "model_ref": be.model_ref,
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "category": "unknown"}


def op_auth_info(params):
    """Retourne les infos nécessaires pour une connexion directe (SSE)."""
    mw = _mw_dir()
    token = (mw / "api.token").read_text().strip()
    port = int((mw / "api.port").read_text().strip())
    return {"token": token, "port": port}


class StreamWriter:
    """Helper SSE : écriture d'events dans un wfile HTTP."""
    def __init__(self, wfile):
        self._wfile = wfile
        self._closed = False

    def send(self, event: str, data: dict):
        if self._closed:
            return
        payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        self._wfile.write(payload.encode())
        self._wfile.flush()

    def error(self, msg: str, category: str = "unknown",
              provider_ref: str = "", model_ref: str = ""):
        self.send("error", {"error": msg, "category": category,
                            "provider_ref": provider_ref, "model_ref": model_ref})

    def done(self):
        if self._closed:
            return
        self.send("done", {"done": True})
        self._closed = True


def op_llm_chat_stream_sse(params, wfile):
    """SSE streaming : itère sur bridge.chat_stream() et écrit les events."""
    bridge = _get_bridge()
    provider_ref = params.get("provider_ref")
    model_ref = params.get("model_ref")
    messages = params.get("messages", [])
    sw = StreamWriter(wfile)
    char_count = 0
    try:
        if not provider_ref or not model_ref or not messages:
            sw.error("provider_ref, model_ref et messages requis")
            sw.done()
            return
        for chunk in bridge.chat_stream(
            provider_ref=provider_ref,
            model_ref=model_ref,
            messages=messages,
            temperature=params.get("temperature", 0.7),
            max_tokens=params.get("max_tokens"),
            system_prompt=params.get("system_prompt"),
        ):
            char_count += len(chunk)
            sw.send("delta", {"content": chunk})
    except BridgeError as be:
        sw.error(be.message, be.category.value, be.provider_ref, be.model_ref)
    except Exception as e:
        sw.error(str(e), "unknown", provider_ref or "", model_ref or "")
    finally:
        sw.done()
        if char_count:
            tokens = max(1, char_count // 4)
            from services.ratelimit import check_rate_limit
            try:
                check_rate_limit("llm/chat/stream", "127.0.0.1", tokens=tokens)
            except Exception:
                pass


def op_llm_capabilities(params):
    """Capacités d'un modèle. params: provider_ref, model_ref"""
    bridge = _get_bridge()
    provider_ref = params.get("provider_ref")
    model_ref = params.get("model_ref")
    if not provider_ref or not model_ref:
        return {"status": "error", "error": "provider_ref et model_ref requis"}
    try:
        caps = bridge.get_capabilities(provider_ref, model_ref)
        return {"status": "ok",
                "context_window": caps.context_window,
                "max_output": caps.max_output,
                "cost_input_per_1k": caps.cost_input_per_1k,
                "cost_output_per_1k": caps.cost_output_per_1k,
                "supports_vision": caps.supports_vision,
                "supports_function_calling": caps.supports_function_calling,
                "mode": caps.mode}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_llm_bridge_status(params):
    """État du bridge et des providers. params: provider_ref (optionnel)"""
    bridge = _get_bridge()
    provider_ref = params.get("provider_ref")
    try:
        if provider_ref:
            return bridge.health_check(provider_ref)
        providers = bridge.list_available_providers()
        return {"status": "ok",
                "bridge": "litellm",
                "providers": providers,
                "count": len(providers)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_llm_context_probe(params):
    """Probe de contexte : vérifie la fenêtre effective. params: provider_ref, model_ref, test_tokens (optionnel)"""
    bridge = _get_bridge()
    provider_ref = params.get("provider_ref")
    model_ref = params.get("model_ref")
    if not provider_ref or not model_ref:
        return {"status": "error", "error": "provider_ref et model_ref requis"}
    try:
        caps = bridge.get_capabilities(provider_ref, model_ref)
        effective = bridge.validator.get_effective_context(provider_ref, model_ref)
        return {"status": "ok",
                "provider_ref": provider_ref,
                "model_ref": model_ref,
                "context_window_announced": caps.context_window,
                "context_window_effective": effective or caps.context_window}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_llm_context_history(params):
    """Historique des dépassements de contexte. params: provider_ref (optionnel), model_ref (optionnel), limit"""
    cat = _get_cat()
    provider_ref = params.get("provider_ref")
    model_ref = params.get("model_ref")
    limit = params.get("limit", 50)
    if provider_ref and model_ref:
        cur = cat.conn.execute(
            "SELECT * FROM context_audit_log WHERE provider_ref=? AND model_ref=? "
            "ORDER BY created_at DESC LIMIT ?",
            (provider_ref, model_ref, limit))
    elif provider_ref:
        cur = cat.conn.execute(
            "SELECT * FROM context_audit_log WHERE provider_ref=? "
            "ORDER BY created_at DESC LIMIT ?",
            (provider_ref, limit))
    else:
        cur = cat.conn.execute(
            "SELECT * FROM context_audit_log ORDER BY created_at DESC LIMIT ?",
            (limit,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return {"status": "ok", "logs": rows, "count": len(rows)}


def op_llm_local_list(params):
    """Liste les moteurs LLM locaux détectés (Ollama, LM Studio, ...)."""
    mgr = get_local_engine_manager()
    return mgr.list_engines()


def op_llm_local_start(params):
    """Démarre un moteur local gérable en headless. params: engine"""
    mgr = get_local_engine_manager()
    engine_ref = params.get("engine")
    if not engine_ref:
        return {"status": "error", "error": "paramètre engine requis"}
    return mgr.start(engine_ref)


def op_llm_local_stop(params):
    """Arrête un moteur local. params: engine"""
    mgr = get_local_engine_manager()
    engine_ref = params.get("engine")
    if not engine_ref:
        return {"status": "error", "error": "paramètre engine requis"}
    return mgr.stop(engine_ref)


def op_llm_local_models(params):
    """Liste les modèles disponibles d'un moteur local. params: engine"""
    mgr = get_local_engine_manager()
    engine_ref = params.get("engine")
    if not engine_ref:
        return {"status": "error", "error": "paramètre engine requis"}
    return mgr.list_models(engine_ref)


# ── Agent Manager ──────────────────────────────────────────

def _get_agent_db() -> AgentsDB:
    d = getattr(_get_agent_db, "_db", None)
    if d is None:
        d = AgentsDB()
        _get_agent_db._db = d
    return d


def op_agent_list(_params):
    """Liste tous les agents (vivants et morts)."""
    db = _get_agent_db()
    rows = db.conn.execute(
        "SELECT agent_id, name, ref, role_type, occupation, status, "
        "       created_at, last_active_at "
        "FROM agents ORDER BY name"
    ).fetchall()
    agents = [dict(r) for r in rows]
    # Enrichir avec runtime si actif
    for a in agents:
        rt = db.conn.execute(
            "SELECT thread_id, heartbeat_at, current_step FROM agent_runtime WHERE agent_id = ?",
            (a["agent_id"],)
        ).fetchone()
        if rt:
            a["running"] = True
            a["thread_id"] = rt["thread_id"]
            a["heartbeat"] = rt["heartbeat_at"]
            a["current_step"] = rt["current_step"]
        else:
            a["running"] = False
    return {"agents": agents, "count": len(agents)}


def op_agent_get(params):
    """Retourne un agent par ID ou name."""
    db = _get_agent_db()
    agent_id = params.get("agent_id")
    name = params.get("name")
    if agent_id:
        row = db.conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    elif name:
        row = db.conn.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
    else:
        return {"status": "error", "error": "agent_id ou name requis"}
    if not row:
        return {"status": "error", "error": "agent introuvable"}
    agent = dict(row)
    # Runtime
    rt = db.conn.execute(
        "SELECT * FROM agent_runtime WHERE agent_id = ?", (agent["agent_id"],)
    ).fetchone()
    agent["runtime"] = dict(rt) if rt else None
    return {"agent": agent}


def op_agent_create(params):
    """Crée un agent en BDD.
    params: name (facultatif, sinon auto role_N), role, occupation?, config?, resources?, variables?
    """
    role = params.get("role")
    if not role:
        return {"status": "error", "error": "role requis"}
    from services.agent_manager.service import AgentManager
    mgr = AgentManager(db=_get_agent_db())
    name = mgr._make_agent_name(_get_agent_db().conn, role, params.get("name", ""))
    ref = f"agent:{name}"
    occupation = params.get("occupation", "noncontinue")
    config_json = json.dumps(params.get("config", {}))
    resources_json = json.dumps(params.get("resources", {}))
    variables_json = json.dumps(params.get("variables", {}))
    try:
        db_conn = _get_agent_db().conn
        db_conn.execute("""
            INSERT INTO agents (name, ref, role_type, occupation, config_json, resources_json, variables_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, ref, role, occupation, config_json, resources_json, variables_json))
        db_conn.commit()
        agent_id = db_conn.execute("SELECT agent_id FROM agents WHERE name = ?", (name,)).fetchone()[0]
        from AgentFrameWork.agent_storage import AgentStorage
        AgentStorage(agent_id, db_conn).ensure()
        from services.audit import audit
        audit("agent.create", agent_id=agent_id, role=role, name=name, ok=True)
        return {"status": "ok", "agent_id": agent_id, "ref": ref}
    except Exception as e:
        from services.audit import audit
        audit("agent.create", role=role, ok=False, error=str(e))
        return {"status": "error", "error": str(e)}


def op_agent_delete(params):
    """Supprime un agent (et son runtime + espace disque)."""
    agent_id = params.get("agent_id")
    name = params.get("name")
    if not agent_id and not name:
        return {"status": "error", "error": "agent_id ou name requis"}
    db = _get_agent_db()
    if name:
        row = db.conn.execute("SELECT agent_id FROM agents WHERE name = ?", (name,)).fetchone()
        if not row:
            return {"status": "error", "error": "agent introuvable"}
        agent_id = row["agent_id"]
    db.conn.execute("DELETE FROM agent_runtime WHERE agent_id = ?", (agent_id,))
    db.conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
    db.conn.commit()
    from AgentFrameWork.agent_storage import AgentStorage
    AgentStorage(agent_id, db.conn).destroy()
    from services.audit import audit
    audit("agent.delete", agent_id=agent_id, name=name, ok=True)
    return {"status": "ok", "agent_id": agent_id}


def op_agent_execute(params):
    """Hydrate un agent et exécute une requête LLM — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    return get_afd_client().call(ref, "execute",
                                 request=params.get("request", ""),
                                 provider_ref=params.get("provider_ref", ""),
                                 model_ref=params.get("model_ref", ""))


def op_agent_manager_status(_params):
    """Retourne le statut de l'AgentManager — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    return get_afd_client().call(None, "manager_status")


def op_agent_evaluate(params):
    """Évalue si un agent PEUT tourner — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    return get_afd_client().call(ref, "evaluate", resources=params.get("resources"))


def op_agent_admit(params):
    """Admission control — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    return get_afd_client().call(ref, "admit")


def op_agent_signal(params):
    """Enfile un signal — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    stype = params.get("type")
    result = get_afd_client().call(ref, "signal", type=stype, payload=params.get("payload"))
    if stype in ("kill", "pause", "resume", "configure"):
        from services.audit import audit
        audit(f"agent.signal.{stype}", agent_id=agent_id, name=name,
              ok=result.get("status") == "ok", payload=params.get("payload"))
    return result


def op_agent_signals(params):
    """Liste les signaux — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    return get_afd_client().call(ref, "signals", status=params.get("status"))


def op_agent_signal_ack(params):
    """Acquittement d'un signal — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    return get_afd_client().call(None, "signal/ack", signal_id=params.get("signal_id"))


def op_agent_signal_complete(params):
    """Clôture d'un signal — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    return get_afd_client().call(None, "signal/complete", signal_id=params.get("signal_id"),
                                 result=params.get("result"))


def op_agent_stream(params):
    """Retourne les chunks diffusés — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    return get_afd_client().call(ref, "stream", seq=params.get("seq", 0))


def op_agent_spawn(params):
    """Spawn d'agent — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    return get_afd_client().call(None, "spawn", **params)


def op_agent_handoff(params):
    """Succession d'agent — proxy vers AFD."""
    from services.api.afd_client import get_afd_client
    return get_afd_client().call(None, "handoff", **params)


# ── N. Chat Service (V0.6.6) : sessions = agents role_type='chat' ──
# Le chat est un agent pur : les opérations sont des façades sur AgentManager
# (qui pilote le framework FSM + StreamBus + signaux). Aucune logique LLM
# dupliquée — services/chat/service.py a été retiré.

def _chat_mgr() -> "AgentManager":
    from services.agent_manager.service import AgentManager
    return AgentManager(db=_get_agent_db())


def op_chat_session_create(params):
    """Crée une session de chat = agent role_type='chat' (params: name,
    provider_ref?, model_ref?, system_prompt?, allow_read_others?)."""
    return _chat_mgr().create_chat_session(
        name=params.get("name"),
        provider_ref=params.get("provider_ref", ""),
        model_ref=params.get("model_ref", ""),
        system_prompt=params.get("system_prompt", ""),
        allow_read_others=bool(params.get("allow_read_others", False)),
    )


def op_chat_session_list(_params):
    """Liste les sessions de chat (agents role_type='chat')."""
    return _chat_mgr().list_chat_sessions()


def op_chat_session_get(params):
    """Récupère une session (params: name)."""
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    return _chat_mgr().get_chat_session(name)


def op_chat_session_delete(params):
    """Supprime une session (params: name)."""
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    return _chat_mgr().delete_chat_session(name)


def op_chat_session_update(params):
    """Met à jour system_prompt / provider / model / allow_read_others (params: name, ...)."""
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    return _chat_mgr().update_chat_session(
        name=name,
        system_prompt=params.get("system_prompt"),
        provider_ref=params.get("provider_ref"),
        model_ref=params.get("model_ref"),
        allow_read_others=params.get("allow_read_others"),
    )


def op_chat_session_send(params):
    """Envoie un message à une session = un tour de chat agentique (params:
    name, message, provider_ref?, model_ref?, stream?, temperature?, max_tokens?)."""
    name = params.get("name")
    message = params.get("message")
    if not name or message is None:
        return {"status": "error", "error": "name et message requis"}
    mt = params.get("max_tokens")
    return _chat_mgr().chat_send(
        name=name, message=message,
        provider_ref=params.get("provider_ref", ""),
        model_ref=params.get("model_ref", ""),
        stream=bool(params.get("stream", False)),
        temperature=float(params.get("temperature", 0.7)),
        max_tokens=int(mt) if mt is not None else None,
    )


def op_chat_session_history(params):
    """Historique d'une session (params: name)."""
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    return _chat_mgr().get_chat_session(name)


def op_chat_session_read(params):
    """Lit l'historique d'une AUTRE session (params: name, other)."""
    name = params.get("name")
    other = params.get("other")
    if not name or not other:
        return {"status": "error", "error": "name et other requis"}
    return _chat_mgr().chat_read(name, other)


def op_chat_session_stream(params):
    """Stream des tokens d'une session (params: name, seq?)."""
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    return op_agent_stream({"name": name, "seq": int(params.get("seq", 0) or 0)})


def op_provider_endpoint_add(params):
    """Ajoute un endpoint à un provider (table provider_endpoints).
    params: provider_ref, label, endpoint_url, api_type?, is_default?"""
    from modules.sql.db import CatalogueDB
    ref = params.get("provider_ref")
    label = params.get("label") or "v1"
    url = params.get("endpoint_url")
    if not ref or not url:
        return {"status": "error", "error": "provider_ref et endpoint_url requis"}
    cat = _get_cat()
    prow = cat.conn.execute(
        "SELECT id FROM catalogue_providers WHERE ref=?", (ref,)).fetchone()
    if not prow:
        return {"status": "error", "error": f"provider inconnu: {ref}"}
    is_default = 1 if params.get("is_default") else 0
    if is_default:
        # un seul endpoint par défaut
        cat.conn.execute(
            "UPDATE provider_endpoints SET is_default=0 WHERE provider_id=?", (prow[0],))
    cat.conn.execute(
        "INSERT INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default) "
        "VALUES (?,?,?,?,?)",
        (prow[0], label, url, params.get("api_type"), is_default))
    cat.conn.commit()
    return {"status": "ok", "provider_ref": ref, "endpoint_url": url}


def _wrap(fn):
    """Adapte une fonction sans args en handler(params)->dict, en silençant
    tout print intempestif vers stderr."""
    def handler(_params):
        with contextlib.redirect_stdout(sys.stderr):
            return fn()
    return handler


# ── Routage dynamique des agents (Agent Framework Daemon) ──
# Les agents sont dynamiques : les routes ne sont pas hardcodées, elles sont
# résolues à runtime par AgentFrameWork.router selon le rôle + l'état.
# Convention : `agent/*` = routes statiques de gestion ; `agents/{id}/*` =
# routes résolues dynamiquement pour UN agent (introspection + dispatch).

def op_agent_capabilities(_params):
    """Catalogue des rôles + skills/capabilities (GET /v1/capabilities)."""
    return router_capabilities()


def _agent_dynamic_route(method: str, parts: List[str], params: dict):
    """Route dynamique `agents/{id}/{sub}` — proxy vers AFD (ou fallback local).

    Si l'AFD est joignable (socket Unix), tous les appels agents passent par
    le processus dédié. Sinon, fallback local (mono-process).
    """
    if len(parts) < 3 or parts[0] != "agents":
        return None
    try:
        agent_id = int(parts[1])
    except ValueError:
        return {"code": 400, "payload": {"error": "bad_agent_id", "agent_id": parts[1]}}
    sub = parts[2]

    # ── Storage (V0.6.8) : reste ici (infra, pas agent) ──
    if sub == "storage":
        return _storage_route(agent_id, method, parts[3:], params)

    # ── Budget (V0.6.11) : interrogation du budget fournisseur/modèle ──
    if sub == "budget":
        return _budget_route(agent_id, method, parts[3:], params)

    # ── FsAuth (V0.6.20) : allowlist d'accès hôte absolu ──
    if sub == "fs_auth":
        return _fs_auth_route(agent_id, method, parts[3:], params)

    if sub == "routes":
        if method != "GET":
            return {"code": 405, "payload": {"error": "method_not_allowed", "method": method}}
        from services.api.afd_client import get_afd_client
        routes = get_afd_client()._local_routes_for(agent_id)
        return {"code": 200, "payload": {
            "agent_id": agent_id,
            "routes": routes,
        }}

    # Résoudre et exécuter via le proxy AFD (socket Unix ou fallback local)
    from services.api.afd_client import get_afd_client
    result = get_afd_client().call(agent_id, sub, **params)
    if result.get("status") == "error" and "code" in result:
        code = result["code"]
        reason = result.get("reason", "unknown")
        return {"code": code, "payload": {"error": "op_not_allowed", "op": sub, "reason": reason}}
    code = 200 if result.get("status") in ("ok", "success") else 500
    return {"code": code, "payload": result}


def _storage_route(agent_id: int, method: str, sub_parts: List[str], params: dict) -> dict:
    """Gère les sous-routes agents/{id}/storage/* (infra, pas agent)."""
    from AgentFrameWork.agent_storage import AgentStorage
    from modules.sql.db import AgentsDB
    st = AgentStorage(agent_id, AgentsDB().conn)
    if sub_parts and sub_parts[0] == "quota" and len(sub_parts) >= 2 and sub_parts[1] == "approve":
        if method != "POST":
            return {"code": 405, "payload": {"error": "method_not_allowed", "method": method}}
        new_max = params.get("max_bytes")
        if not new_max:
            return {"code": 400, "payload": {"error": "max_bytes requis"}}
        st.approve_quota_request(int(new_max))
        from services.audit import audit
        audit("storage.quota.approve", agent_id=agent_id, new_max=int(new_max), ok=True)
        return {"code": 200, "payload": {"status": "ok", "agent_id": agent_id,
                                          "max_bytes": st.max_bytes,
                                          "used_bytes": st.used_bytes,
                                          "quota_request": None}}
    if method == "POST":
        st.recalc_used()
    return {"code": 200, "payload": {
        "agent_id": agent_id,
        "max_bytes": st.max_bytes,
        "used_bytes": st.used_bytes,
        "quota_request": st.quota_request(),
    }}


def _budget_route(agent_id: int, method: str, sub_parts: List[str], params: dict) -> dict:
    """agents/{id}/budget — interroge le budget restant du fournisseur/modèle de l'agent."""
    if sub_parts:
        return {"code": 404, "payload": {"error": "not_found"}}
    from modules.sql.db import AgentsDB
    row = AgentsDB().conn.execute(
        "SELECT config_json FROM agents WHERE agent_id=?",
        (agent_id,)
    ).fetchone()
    if not row:
        return {"code": 404, "payload": {"error": "agent_not_found"}}
    config = json.loads(row["config_json"] or "{}")
    provider_ref = config.get("provider_ref") or ""
    model_ref = config.get("model_ref") or ""
    if not provider_ref or not model_ref:
        return {"code": 200, "payload": {"agent_id": agent_id, "budget": {}}}
    from services.tarif import check_budget, _get as _get_tarif
    _get_tarif().seed_default()
    budget = check_budget(provider_ref, model_ref)
    return {"code": 200, "payload": {"agent_id": agent_id, "provider_ref": provider_ref,
                                      "model_ref": model_ref, "budget": budget}}


def _fs_auth_route(agent_id: int, method: str, sub_parts: List[str], params: dict) -> dict:
    """agents/{id}/fs_auth — gestion de l'allowlist d'accès hôte (FsAuthManager).

    GET    agents/{id}/fs_auth          -> liste des racines autorisées
    POST   agents/{id}/fs_auth          -> grant (root_path, mode=r|rw)
    DELETE agents/{id}/fs_auth          -> revoke (root_path)
    """
    from services.fs_auth import FsAuthManager
    try:
        mgr = FsAuthManager()
    except Exception as e:
        return {"code": 500, "payload": {"error": f"fs_auth indispo: {e}"}}
    try:
        if method == "GET":
            return {"code": 200, "payload": {"agent_id": agent_id, "grants": mgr.list(agent_id)}}
        if method == "POST":
            root = params.get("root_path")
            mode = params.get("mode", "r")
            if not root:
                return {"code": 400, "payload": {"error": "root_path requis"}}
            mgr.grant(agent_id, root, mode)
            return {"code": 200, "payload": {"status": "ok", "agent_id": agent_id,
                                             "root_path": os.path.abspath(root),
                                             "mode": "rw" if mode == "rw" else "r"}}
        if method == "DELETE":
            root = params.get("root_path")
            if not root:
                return {"code": 400, "payload": {"error": "root_path requis"}}
            mgr.revoke(agent_id, root)
            return {"code": 200, "payload": {"status": "ok", "agent_id": agent_id,
                                             "root_path": os.path.abspath(root),
                                             "revoked": True}}
        return {"code": 405, "payload": {"error": "method_not_allowed", "method": method}}
    finally:
        mgr.close()


# ── Table de routage : "domaine/action" -> handler(params) -> dict ──
ROUTES = {
    # A. Système & environnement
    "system/info":            op_system_info,
    "system/deps/check":      _wrap(check_python_deps),
    "system/state/get":       _wrap(sysstate.get_system_state),
    "system/state/save":      _wrap(save_system_state),
    # B. Bases
    "db/init":                _wrap(init_databases),
    "db/check":               _wrap(check_databases),
    # C. Catalogue
    "catalogue/tools/list":   _wrap(get_catalogue_tools),
    "catalogue/seed":         _wrap(seed_catalogue),
    "catalogue/sync":         lambda p: _quiet(sync_catalogue_remote, p.get("url")),
    "catalogue/tools_table/update": _wrap(update_tools_table),
    "catalogue/fetch/remote":  lambda p: _quiet(fetch_remote_to_local),
    # D. Outils installés (synchrone)
    "tools/installed/list":   _wrap(get_installed_tools),
    "tools/install":          lambda p: _quiet(jobs.install_tool, p.get("ref"), None, _get_cat()),
    "tools/uninstall":        lambda p: _quiet(jobs.uninstall_tool, p.get("ref"), None, _get_cat()),
    "tools/install/all":      lambda p: _quiet(op_tools_install_all, p),
    # E. File de jobs (asynchrone)
    "jobs/add":               op_jobs_add,
    # F. Dépendances (modules/services)
    "deps/check":             op_deps_check,
    "deps/install":           op_deps_install,
    "deps/install_target":    op_deps_install_target,
    "deps/check_manifest":    op_deps_check_manifest,
    "db/versions":            op_db_versions,
    "jobs/list":              op_jobs_list,
    "jobs/status":            op_jobs_status,
    "jobs/cancel":            op_jobs_cancel,
    "jobs/clear":             op_jobs_clear,
    # G. Key Manager
    "keys/set":               op_keys_set,
    "keys/get":               op_keys_get,
    "keys/list":              op_keys_list,
    "keys/delete":            op_keys_delete,
    "keys/set_lock":          op_keys_set_lock,
    "keys/onboard":           op_keys_onboard,
    # H. Providers (catalogue)
    "providers/list":         op_providers_list,
    "provider/endpoint/add":  op_provider_endpoint_add,
    # I. LLM Manager
    "llm/models/list":        op_llm_models_list,
    "llm/recommend":          op_llm_recommend,
    # K. LLM Bridge
    "llm/chat":               op_llm_chat,
    "llm/chat/stream":        op_llm_chat,  # fallback JSON
    "llm/capabilities":       op_llm_capabilities,
    "llm/bridge/status":      op_llm_bridge_status,
    "llm/context/probe":      op_llm_context_probe,
    "llm/context/history":    op_llm_context_history,
    # K2. LLM locaux (moteurs détectés sur la machine)
    "llm/local/list":         op_llm_local_list,
    "llm/local/start":        op_llm_local_start,
    "llm/local/stop":         op_llm_local_stop,
    "llm/local/models":       op_llm_local_models,
    # L. Auth / Infra
    "auth/info":              op_auth_info,
    # J. Logs
    "logs/read":              op_logs_read,
    "logs/write":             op_logs_write,
    # M. Agent Manager
    "agent/list":             op_agent_list,
    "agent/get":              op_agent_get,
    "agent/create":           op_agent_create,
    "agent/delete":           op_agent_delete,
    "agent/execute":          op_agent_execute,
    "agent/manager/status":   op_agent_manager_status,
    "agent/resources/evaluate": op_agent_evaluate,
    "agent/admit":            op_agent_admit,
    "agent/signal":           op_agent_signal,
    "agent/signals":          op_agent_signals,
    "agent/signal/ack":       op_agent_signal_ack,
    "agent/signal/complete":  op_agent_signal_complete,
    "agent/stream":           op_agent_stream,
    "agent/spawn":            op_agent_spawn,
    "agent/handoff":          op_agent_handoff,
    # N. Chat Service (V0.6.6)
    "chat/session/create":    op_chat_session_create,
    "chat/session/list":      op_chat_session_list,
    "chat/session/get":       op_chat_session_get,
    "chat/session/delete":    op_chat_session_delete,
    "chat/session/update":    op_chat_session_update,
    "chat/session/send":      op_chat_session_send,
    "chat/session/history":   op_chat_session_history,
    "chat/session/read":      op_chat_session_read,
    "chat/session/stream":    op_chat_session_stream,
    # O. Tarif / budget
    "tarif/info":             lambda p: _quiet(_op_tarif_info, p),
    "tarif/sync":             lambda p: _quiet(_op_tarif_sync, p.get("url")),
}

# Routes qui reçoivent (params, wfile) au lieu de (params) -> dict
# pour la réponse SSE directe (text/event-stream).
STREAMING_ROUTES = {
    "llm/chat/stream":        op_llm_chat_stream_sse,
}


def _quiet(fn, *args):
    with contextlib.redirect_stdout(sys.stderr):
        return fn(*args)


class MWAPIHandler(BaseHTTPRequestHandler):
    server_version = "ModelWeaverDaemon/1.0"

    def log_message(self, *args):
        pass  # silence le logging par défaut sur stderr

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.token}"
        return secrets.compare_digest(auth, expected)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "version": MW_VERSION, "api": API_VERSION})
            return
        prefix = f"/{API_VERSION}/"
        if not self.path.startswith(prefix):
            self._send(404, {"error": "not_found", "path": self.path})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        route = self.path[len(prefix):].strip("/")
        parts = [p for p in route.split("/") if p]
        # Rate limiting
        client_ip = self.client_address[0]
        try:
            from services.ratelimit import check_rate_limit
            check_rate_limit(route, client_ip)
        except Exception as e:
            self._send(429, {"error": "rate_limited", "detail": str(e)})
            return
        # Route dynamique agents/{id}/routes ?
        dyn = _agent_dynamic_route("GET", parts, {})
        if dyn is not None:
            self._send(dyn["code"], {"ok": dyn["code"] == 200,
                                     "route": route, "result": dyn["payload"]})
            return
        # Catalogue des capacités (rôles/skills) ?
        if route == "capabilities":
            self._send(200, {"ok": True, "route": route, "result": router_capabilities()})
            return
        self._send(404, {"error": "not_found", "path": self.path})

    def _handle_stream(self, route: str, handler, params: dict):
        """SSE : envoie les headers puis délègue l'itération au handler(stream, params, wfile)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            handler(params, self.wfile)
        except Exception as e:
            import traceback
            err = json.dumps({"error": str(e), "trace": traceback.format_exc()})
            self.wfile.write(f"event: error\ndata: {err}\n\n".encode())
            self.wfile.write(b"event: done\ndata: {\"done\":true}\n\n")
        finally:
            try:
                self.wfile.flush()
            except Exception:
                pass
            self.close_connection = True

    def do_POST(self):
        prefix = f"/{API_VERSION}/"
        if not self.path.startswith(prefix):
            self._send(404, {"error": "not_found", "path": self.path})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        route = self.path[len(prefix):].strip("/")
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            params = json.loads(raw) if raw else {}
        except Exception as e:
            self._send(400, {"error": "bad_request", "detail": str(e)})
            return
        # Rate limiting
        client_ip = self.client_address[0]
        try:
            from services.ratelimit import check_rate_limit
            check_rate_limit(route, client_ip)
        except Exception as e:
            self._send(429, {"error": "rate_limited", "detail": str(e)})
            return
        # Streaming SSE ?
        stream_handler = STREAMING_ROUTES.get(route)
        if stream_handler:
            self._handle_stream(route, stream_handler, params)
            return
        # Route dynamique agents/{id}/{op} ?
        parts = [p for p in route.split("/") if p]
        dyn = _agent_dynamic_route("POST", parts, params)
        if dyn is not None:
            self._send(dyn["code"], {"ok": dyn["code"] == 200,
                                     "route": route, "result": dyn["payload"]})
            return
        # JSON normal
        handler = ROUTES.get(route)
        if not handler:
            self._send(404, {"error": "unknown_route", "route": route})
            return
        try:
            result = handler(params)
            self._send(200, {"ok": True, "route": route, "result": result})
        except Exception as e:
            import traceback
            self._send(500, {"ok": False, "route": route, "error": str(e),
                             "trace": traceback.format_exc()})

    def do_DELETE(self):
        prefix = f"/{API_VERSION}/"
        if not self.path.startswith(prefix):
            self._send(404, {"error": "not_found", "path": self.path})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        route = self.path[len(prefix):].strip("/")
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            params = json.loads(raw) if raw else {}
        except Exception as e:
            self._send(400, {"error": "bad_request", "detail": str(e)})
            return
        parts = [p for p in route.split("/") if p]
        dyn = _agent_dynamic_route("DELETE", parts, params)
        if dyn is not None:
            self._send(dyn["code"], {"ok": dyn["code"] == 200,
                                     "route": route, "result": dyn["payload"]})
            return
        self._send(404, {"error": "unknown_route", "route": route})


def serve(port: int = 8770) -> None:
    """Point d'entrée du service `api` (supervisé). Un seul daemon à la fois."""
    from services._common import acquire_instance_lock
    if not acquire_instance_lock("api"):
        print("❌ daemon déjà en cours (lock api)", file=sys.stderr)
        sys.exit(1)

    from services.logger import MWLogger
    log = MWLogger("daemon")

    mw = _mw_dir()
    token = secrets.token_hex(32)
    token_file = mw / "api.token"
    token_file.write_text(token)
    os.chmod(token_file, 0o600)

    # bind avec retry (port occupé au boot)
    server = None
    for attempt in range(10):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), MWAPIHandler)
            break
        except OSError as e:
            log.warning("Bind échoué", port=port, attempt=attempt+1, error=str(e))
            time.sleep(1)
    if server is None:
        log.critical("Impossible de binder le daemon", port=port)
        sys.exit(1)

    server.token = token
    (mw / "api.port").write_text(str(port))

    # Initialiser les connexions partagées AVANT le processeur de jobs.
    mw_singleton = _get_mw()
    cat_singleton = _get_cat()
    # Charger les clés en mémoire (après validation keyring OS).
    try:
        _get_km().load()
    except Exception as e:
        log.warning("Keyring indisponible", error=str(e))
    # busy_timeout 30s pour les opérations concurrentes en arrière-plan
    mw_singleton.conn.execute("PRAGMA busy_timeout = 30000")

    # Au démarrage : si la table locale des outils installés est vide,
    # lancer une détection une fois. Les outils installés hors flux catalogue
    # (ex. dépendances système installées via `deps/install`) doivent
    # apparaître immédiatement dans la GUI sans attendre un install/uninstall.
    try:
        if len(mw_singleton.local_tools.list_all()) == 0:
            mw_singleton.scan_installed_tools()
            mw_singleton.commit()
    except Exception as e:
        log.warning("Scan outils installés échoué", error=str(e))

    # Injecter la connexion partagée dans le module jobs (évite les locks).
    jobs.set_shared_conn(mw_singleton.conn)

    # Démarre le processeur de jobs en arrière-plan (consomme install_jobs).
    _job_processor_loop(interval=3.0)

    # Activer le StreamBus cross-process (partagé avec l'AFD si démarré)
    try:
        from AgentFrameWork.stream_bus import activate_cross_process, resolve_stream_path
        activate_cross_process(resolve_stream_path())
    except Exception as e:
        log.warning("StreamBus cross-process échoué", error=str(e))

    log.info("Daemon démarré", port=port, api=API_VERSION, version=MW_VERSION,
             token=str(token_file), routes=len(ROUTES))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Arrêt du daemon")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", nargs="?", default="serve",
                        help="commande (serve par défaut)")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    # `serve` est la commande par défaut ; tout autre argument positionnel
    # inconnu est ignoré (compatibilité avec les superviseurs qui le passent).
    if args.cmd not in ("serve", None):
        pass
    serve(args.port)


if __name__ == "__main__":
    main()
