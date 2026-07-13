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
from modules.sql.db import ModelWeaverDB, CatalogueDB, RuntimeDB, read_db_version, fetch_remote_to_local
from modules.checker.checker import Checker
from services._common import _db_paths, _quiet_stdout, log_to_file, runtime_db_path

API_VERSION = "v1"
MW_VERSION = "0.6.0"


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
        print(f"⚠️  seed_recipes échoué : {e}", file=sys.stderr)
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
        print(f"⚠️  rescan outils installés échoué : {e}", file=sys.stderr)


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
    if ref:
        ok = km.delete_key(ref)
        return {"status": "ok" if ok else "error", "deleted": ok}
    if provider_ref:
        keys = km.list_keys()
        deleted = 0
        for k in keys:
            if k.get("provider_ref") == provider_ref:
                if km.delete_key(k["ref"]):
                    deleted += 1
        return {"status": "ok", "deleted": deleted}
    return {"status": "error", "error": "missing 'ref' or 'provider_ref'"}


def op_keys_onboard(params):
    from modules.key_manager.onboarder import Onboarder
    km = _get_km()
    onboarder = Onboarder(km)
    env_path = params.get("env_path", str(_REPO_ROOT / ".env"))
    count = onboarder.onboard_from_env(Path(env_path))
    return {"status": "ok", "imported": count}


def op_providers_list(_params):
    from modules.sql.db import CatalogueDB
    cat = _get_cat()
    cur = cat.conn.execute(
        "SELECT ref, name, provider_type, api_type, website, is_free_tier_provider "
        "FROM catalogue_providers ORDER BY name")
    cols = [d[0] for d in cur.description]
    providers = [dict(zip(cols, row)) for row in cur.fetchall()]
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


def _wrap(fn):
    """Adapte une fonction sans args en handler(params)->dict, en silençant
    tout print intempestif vers stderr."""
    def handler(_params):
        with contextlib.redirect_stdout(sys.stderr):
            return fn()
    return handler


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
    # I. LLM Manager
    "llm/models/list":        op_llm_models_list,
    "llm/recommend":          op_llm_recommend,
    # J. Logs
    "logs/read":              op_logs_read,
    "logs/write":             op_logs_write,
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
        self._send(404, {"error": "not_found", "path": self.path})

    def do_POST(self):
        prefix = f"/{API_VERSION}/"
        if not self.path.startswith(prefix):
            self._send(404, {"error": "not_found", "path": self.path})
            return
        if not self._authorized():
            self._send(401, {"error": "unauthorized"})
            return
        route = self.path[len(prefix):].strip("/")
        handler = ROUTES.get(route)
        if not handler:
            self._send(404, {"error": "unknown_route", "route": route})
            return
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            params = json.loads(raw) if raw else {}
        except Exception as e:
            self._send(400, {"error": "bad_request", "detail": str(e)})
            return
        try:
            result = handler(params)
            self._send(200, {"ok": True, "route": route, "result": result})
        except Exception as e:
            import traceback
            self._send(500, {"ok": False, "route": route, "error": str(e),
                             "trace": traceback.format_exc()})


def serve(port: int = 8770) -> None:
    """Point d'entrée du service `api` (supervisé). Un seul daemon à la fois."""
    from services._common import acquire_instance_lock
    if not acquire_instance_lock("api"):
        print("❌ daemon déjà en cours (lock api)", file=sys.stderr)
        sys.exit(1)

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
            print(f"⚠️  bind {port} échoué ({e}), retry {attempt + 1}/10...", file=sys.stderr)
            time.sleep(1)
    if server is None:
        print("❌ impossible de binder le daemon", file=sys.stderr)
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
        print(f"⚠️  Keyring indisponible (clés non chargées) : {e}", file=sys.stderr)
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
        print(f"⚠️  scan outils installés (démarrage) échoué : {e}", file=sys.stderr)

    # Injecter la connexion partagée dans le module jobs (évite les locks).
    jobs.set_shared_conn(mw_singleton.conn)

    # Démarre le processeur de jobs en arrière-plan (consomme install_jobs).
    _job_processor_loop(interval=3.0)

    print(f"✅ ModelWeaver daemon — http://127.0.0.1:{port}  (api {API_VERSION})", file=sys.stderr)
    print(f"   token : {token_file}", file=sys.stderr)
    print(f"   routes: {len(ROUTES)}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("arrêt du daemon", file=sys.stderr)


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
