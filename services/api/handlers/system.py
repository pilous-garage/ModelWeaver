import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Dict, Any

from services._common import _quiet_stdout, log_to_file, _db_paths
from services.installer_worker import jobs
from services.api._shared import (
    API_VERSION, MW_VERSION, repo_root,
    _mw_dir, _get_mw, _get_cat, _get_rt, _get_km,
    _wrap, _quiet,
)
from services.api.router import register

# ── Opérateurs ──────────────────────────────────────────────────────────

def op_system_info(_params):
    return {
        "os": sys.platform,
        "system": platform.system(),
        "arch": platform.machine(),
        "home": str(Path.home()),
        "python": platform.python_version(),
    }


def op_version(_params):
    return {"version": MW_VERSION, "api": API_VERSION}


def op_system_hardware():
    """Inventaire matériel complet (check système) — CPU, RAM, carte mère,
    GPU, disques, réseau, USB, températures."""
    from modules.checker.hardware_check import full_inventory
    return full_inventory()


def op_system_resources(_params=None):
    """Snapshot runtime : GPU (charge/temp), bande passante réseau."""
    from modules.checker.hardware_check import runtime_sample
    return runtime_sample()


def op_system_processes(_params=None):
    """Top processus par CPU/RAM (psutil), triés par utilisation CPU."""
    try:
        import psutil
        out = []
        for p in psutil.process_iter(["pid", "name", "cpu_percent",
                                      "memory_info", "cmdline"]):
            try:
                info = p.info
                cmd = info.get("cmdline") or []
                out.append({
                    "pid": info["pid"],
                    "name": info.get("name") or "",
                    "cpu": round(info.get("cpu_percent") or 0.0, 1),
                    "rss_kb": round((info.get("memory_info") or {}).rss / 1024),
                    "command": " ".join(cmd)[:160] if cmd else "",
                })
            except Exception:
                continue
        out.sort(key=lambda x: x["cpu"], reverse=True)
        return {"processes": out[:30], "count": len(out)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def check_python_deps():
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
    from modules.sql.sql_module import _ensure_classes_outils_table, resolve_classe_id, _default_class_for_ref
    data_path = str(repo_root() / "modules" / "catalogue" / "data" / "tools.json")
    try:
        with open(data_path) as f:
            tools = json.load(f)
    except Exception:
        tools = []
    if isinstance(tools, dict):
        tools = tools.get("tools", [])
    try:
        _ensure_classes_outils_table(cat.conn)
    except Exception:
        pass
    for t in tools:
        ref = t.get("ref")
        if not ref:
            continue
        classe_ref = t.get("classe") or _default_class_for_ref(ref)
        try:
            classe_id = resolve_classe_id(cat.conn, classe_ref)
        except Exception:
            classe_id = None
        cat.conn.execute(
            "INSERT OR IGNORE INTO catalogue_outils (ref, nom, description, tool_type, classe_outil_id) VALUES (?,?,?,?,?)",
            (ref, t.get("name", ref), t.get("description", ""), t.get("tool_type"), classe_id))
    cat.conn.commit()
    recipe_dir = repo_root() / "modules" / "installer" / "install_recipe"
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
    from modules.llm_manager.llm_manager_module import seed_providers, seed_models, seed_provider_models
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
    data_path = str(repo_root() / "modules" / "catalogue" / "data" / "tools.json")
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
    from modules.checker.checker_module import Checker
    Checker().update_local_db(mw)
    mw.commit()
    return {"status": "ok"}


def system_services_list():
    """Liste des services gérés par le superviseur (PID, socket, statut)."""
    try:
        from services.supervisor.client import get_supervisor_client
        res = get_supervisor_client().list_services()
        return res.get("services", {})
    except Exception as e:
        return {"status": "error", "error": str(e)}


def system_service_restart(name: str):
    """Redémarre un service via le superviseur."""
    if not name:
        return {"status": "error", "error": "name requis"}
    try:
        from services.supervisor.client import get_supervisor_client
        return get_supervisor_client().restart(name)
    except Exception as e:
        return {"status": "error", "error": str(e)}


def system_service_start(name: str):
    """Démarre un service via le superviseur."""
    if not name:
        return {"status": "error", "error": "name requis"}
    try:
        from services.supervisor.client import get_supervisor_client
        return get_supervisor_client().start(name)
    except Exception as e:
        return {"status": "error", "error": str(e)}


def system_service_stop(name: str):
    """Arrête un service via le superviseur."""
    if not name:
        return {"status": "error", "error": "name requis"}
    try:
        from services.supervisor.client import get_supervisor_client
        return get_supervisor_client().stop(name)
    except Exception as e:
        return {"status": "error", "error": str(e)}


def system_service_reset(name: str):
    """Reset complet d'un service (arrêt + relance à froid)."""
    if not name:
        return {"status": "error", "error": "name requis"}
    try:
        from services.supervisor.client import get_supervisor_client
        return get_supervisor_client().reset(name)
    except Exception as e:
        return {"status": "error", "error": str(e)}


def system_services_reset_all():
    """Reset complet de tous les services gérés."""
    try:
        from services.supervisor.client import get_supervisor_client
        return get_supervisor_client().reset_all()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def system_shutdown():
    """Arrêt complet : stoppe tous les services via le superviseur."""
    try:
        from services.supervisor.client import get_supervisor_client
        return get_supervisor_client().shutdown()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def system_shutdown_all():
    """Arrêt total durable : stoppe tous les services + le superviseur,
    et empêche le daemon de le relancer (flag supervisor.disabled)."""
    try:
        from services.supervisor.client import get_supervisor_client
        return get_supervisor_client().shutdown_all()
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_system_state_get():
    """État système complet (statique + hardware temps réel).

    Statique : OS, arch, gestionnaires détectés.
    Temps réel : RAM/Disque/CPU (global + par cœur).
    """
    from modules.checker.checker import Checker
    checker = Checker()
    info = checker.get_system_info()
    return {
        **info,
        "detected_managers": checker.get_detected_managers(),
        **checker.get_hardware_info(),
    }


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


def model_sync_run_once(_params=None):
    """Lance un cycle manuel du synchroniseur de modèles (par clé API)."""
    from services.model_sync.model_sync import sync_once
    summary = sync_once()
    return {"status": "ok", "summary": summary}


def watcher_run_once(_params=None):
    """Lance un cycle manuel du surveillant du swarm (problem → solution)."""
    from services.watcher.watcher import watcher_cycle
    actions = watcher_cycle()
    return {"status": "ok", "actions": actions}


def update_tools_table():
    count = _get_mw().scan_installed_tools()
    return {"status": "ok", "updated": count}


def op_tools_install_all(_params):
    from modules.checker.checker_module import Checker
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


def _rescan_local_tools():
    try:
        mw = _get_mw()
        mw.scan_installed_tools()
        mw.commit()
    except Exception as e:
        from services.logger import MWLogger; MWLogger("daemon").warning("rescan outils installés échoué", error=str(e))


# ── Dépendances ─────────────────────────────────────────────────────────

def op_deps_check(_params):
    from services.system_service import check_deps
    return check_deps(_params)


def op_deps_install(params):
    from services.system_service import install_dep
    res = install_dep(params)
    if res.get("status") == "ok":
        try:
            _get_rt().bump_meta("dependencies")
        except Exception:
            pass
        _rescan_local_tools()
    return res


def op_deps_install_target(params):
    from services.system_service import install_target
    res = install_target(params)
    if res.get("status") == "ok":
        try:
            _get_rt().bump_meta("dependencies")
        except Exception:
            pass
        _rescan_local_tools()
    return res


def op_db_versions(params):
    from services.sql_service import get_db_versions
    return get_db_versions(params)


_CHECK_CACHE: dict = {}
_CHECK_CACHE_TTL = 30


def op_deps_check_manifest(params):
    import time as _time
    now = _time.time()
    c = _CHECK_CACHE.get("result")
    if c and (now - _CHECK_CACHE.get("ts", 0)) < _CHECK_CACHE_TTL:
        return c
    from modules.system import deps as deps_mod
    target = params.get("target", "") or ""
    try:
        if not target:
            target = deps_mod.detect_target()
        if not target:
            return {"status": "error", "error": "cible non détectée"}
        m = deps_mod.load_manifest()
        deps = [dict(d, _target=target) for d in m.get("dependencies", [])
                if d.get("targets", {}).get(target)]
        status_map = deps_mod.batch_check_dependencies(deps)
        out = []
        for dep in deps:
            pkg = dep.get("targets", {}).get(target)
            installed = status_map.get(pkg, False)
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
        result = {"target": target, "dependencies": out}
        _CHECK_CACHE["result"] = result
        _CHECK_CACHE["ts"] = now
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Logs ────────────────────────────────────────────────────────────────

def op_logs_read(_params):
    log_file = _mw_dir() / "logs" / "installer.log"
    try:
        return {"status": "ok", "log": log_file.read_text(encoding="utf-8", errors="replace")}
    except Exception as e:
        return {"status": "ok", "log": "", "note": str(e)}


def op_logs_write(params):
    log_to_file(params.get("level", "INFO"), params.get("message", ""))
    return {"status": "ok"}


def op_logs_gui_write(params):
    """Écrit un lot d'événements GUI (tracing) dans ~/.modelweaver/logs/gui.log.

    Le frontend V2 envoie un tableau d'événements (batched, flush périodique) :
    { events: [{ts, type, detail}] }. Chaque événement est une ligne JSON.
    """
    try:
        from datetime import datetime
        from services._common import mw_home
        log_dir = mw_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        events = params.get("events") or []
        if not events:
            return {"status": "ok", "count": 0}
        with open(log_dir / "gui.log", "a", encoding="utf-8") as f:
            for ev in events:
                ts = ev.get("ts", datetime.now().isoformat())
                line = f"{ts} [{ev.get('level','INFO')}] {ev.get('type','?')} {ev.get('detail','')}"
                f.write(line + "\n")
        return {"status": "ok", "count": len(events)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Providers ───────────────────────────────────────────────────────────

def op_providers_list(_params):
    from modules.sql.sql_module import CatalogueDB
    cat = _get_cat()
    cur = cat.conn.execute(
        "SELECT ref, name, provider_type, api_type, website, is_free_tier_provider "
        "FROM catalogue_providers ORDER BY name")
    cols = [d[0] for d in cur.description]
    providers = [dict(zip(cols, row)) for row in cur.fetchall()]
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
    try:
        km = _get_km()
        has_key_refs = set(km.list_providers())
        for p in providers:
            p["has_key"] = p["ref"] in has_key_refs
    except Exception:
        for p in providers:
            p["has_key"] = False
    return {"providers": providers, "count": len(providers)}


def op_provider_endpoint_add(params):
    from modules.sql.sql_module import CatalogueDB


def op_alias_model_list(params):
    """Liste les alias de modèles (alias_model) : matching source → notre modèle.

    Filtres optionnels : source_type (provider|benchmark), source, status
    (linked|unresolved|ambiguous), q (recherche plein texte sur source_name),
    limit / offset (pagination).
    """
    cat = _get_cat()
    source_type = params.get("source_type", "")
    source = params.get("source", "")
    status = params.get("status", "")
    q = (params.get("q") or "").strip()
    limit = min(int(params.get("limit", 500)), 5000)
    offset = int(params.get("offset", 0))

    where = []
    args = []
    if source_type:
        where.append("a.source_type = ?")
        args.append(source_type)
    if source:
        where.append("a.source = ?")
        args.append(source)
    if status:
        where.append("a.status = ?")
        args.append(status)
    if q:
        where.append("a.source_name LIKE ?")
        args.append(f"%{q}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    try:
        cur = cat.conn.execute(f"""
            SELECT a.id, a.model_id, a.source_name, a.source, a.source_type,
                   a.confidence, a.status, a.updated_at,
                   COALESCE(cm.model_key, cm.ref, '') AS model_key
            FROM alias_model a
            LEFT JOIN catalogue_models cm ON cm.id = a.model_id
            {where_sql}
            ORDER BY a.source_type, a.source, a.source_name
            LIMIT ? OFFSET ?
        """, (*args, limit, offset))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        total = cat.conn.execute(
            f"SELECT COUNT(*) FROM alias_model a {where_sql}", args).fetchone()[0]
        return {"aliases": rows, "count": len(rows), "total": total,
                "limit": limit, "offset": offset}
    except Exception as e:
        return {"status": "error", "error": str(e), "aliases": []}


def op_alias_model_link(params):
    """Relie manuellement un alias unresolved → un model_id du catalogue.

    params : id (alias id) OU (source_name + source + source_type),
             model_id (catalogue_models.id) ou model_key / model_ref.
    """
    cat = _get_cat()
    alias_id = params.get("id")
    model_id = params.get("model_id")
    ref = (params.get("model_key") or params.get("model_ref") or "").strip()

    if not model_id and ref:
        row = cat.conn.execute(
            "SELECT id FROM catalogue_models WHERE model_key = ? OR ref = ? LIMIT 1",
            (ref, ref)).fetchone()
        if row:
            model_id = row["id"]
        else:
            return {"status": "error", "error": f"modèle introuvable: {ref}"}
    if not model_id:
        return {"status": "error", "error": "model_id ou model_key/model_ref requis"}

    if alias_id:
        cur = cat.conn.execute(
            "UPDATE alias_model SET model_id = ?, status = 'linked', "
            "confidence = 'manual', updated_at = strftime('%s','now') WHERE id = ?",
            (model_id, alias_id))
        cat.conn.commit()
        if cur.rowcount == 0:
            return {"status": "error", "error": f"alias {alias_id} introuvable"}
        return {"status": "ok", "linked": int(alias_id), "model_id": model_id}
    # Sinon par identité (source, source_type, source_name)
    sn = (params.get("source_name") or "").strip()
    src = (params.get("source") or "").strip()
    st = (params.get("source_type") or "").strip()
    if not (sn and src and st):
        return {"status": "error", "error": "id ou (source_name+source+source_type) requis"}
    cat.conn.execute("""
        INSERT OR REPLACE INTO alias_model
            (model_id, source_name, source, source_type, confidence, status, updated_at)
        VALUES (?, ?, ?, ?, 'manual', 'linked', strftime('%s','now'))
    """, (model_id, sn, src, st))
    cat.conn.commit()
    return {"status": "ok", "linked": True, "model_id": model_id}


def op_alias_model_unlink(params):
    """Repasse un alias en unresolved (délie le model_id)."""
    cat = _get_cat()
    alias_id = params.get("id")
    if not alias_id:
        return {"status": "error", "error": "id requis"}
    cur = cat.conn.execute(
        "UPDATE alias_model SET status = 'unresolved', updated_at = strftime('%s','now') "
        "WHERE id = ?", (alias_id,))
    cat.conn.commit()
    return {"status": "ok" if cur.rowcount else "error",
            "unlinked": bool(cur.rowcount)}


def op_alias_model_rebuild(_params):
    """Reconstruit la table alias_model depuis provider_models + model_benchmarks_raw."""
    import subprocess, sys as _sys
    script = Path(repo_root()) / "scripts" / "build_aliases.py"
    try:
        r = subprocess.run([_sys.executable, str(script)],
                           capture_output=True, text=True, timeout=300)
        return {"status": "ok", "stdout": r.stdout[-2000:],
                "stderr": r.stderr[-2000:]}
    except Exception as e:
        return {"status": "error", "error": str(e)}
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
        cat.conn.execute(
            "UPDATE provider_endpoints SET is_default=0 WHERE provider_id=?", (prow[0],))
    cat.conn.execute(
        "INSERT INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default) "
        "VALUES (?,?,?,?,?)",
        (prow[0], label, url, params.get("api_type"), is_default))
    cat.conn.commit()
    return {"status": "ok", "provider_ref": ref, "endpoint_url": url}


# ── Route registration ─────────────────────────────────────────────────

register("system/info",              op_system_info)
register("system/hardware",          _wrap(op_system_hardware))
register("system/resources",         _wrap(op_system_resources))
register("system/processes",         _wrap(op_system_processes))
register("version",                  op_version)
register("system/deps/check",        _wrap(check_python_deps))
register("system/state/get",         _wrap(op_system_state_get))
register("system/state/save",        _wrap(save_system_state))
register("system/services",          _wrap(system_services_list))
register("system/services/restart",  lambda p: _quiet(system_service_restart, p.get("name")))
register("system/services/start",    lambda p: _quiet(system_service_start, p.get("name")))
register("system/services/stop",     lambda p: _quiet(system_service_stop, p.get("name")))
register("system/services/reset",    lambda p: _quiet(system_service_reset, p.get("name")))
register("system/services/reset_all",_wrap(system_services_reset_all))
register("system/shutdown",          _wrap(system_shutdown))
register("system/shutdown_all",      _wrap(system_shutdown_all))
register("db/init",                  _wrap(init_databases))
register("db/check",                 _wrap(check_databases))
register("catalogue/tools/list",     _wrap(get_catalogue_tools))
register("catalogue/seed",           _wrap(seed_catalogue))
register("catalogue/sync",           lambda p: _quiet(sync_catalogue_remote, p.get("url")))
register("catalogue/models/sync",    lambda p: _quiet(model_sync_run_once))
register("watcher/run",              lambda p: _quiet(watcher_run_once))
register("catalogue/tools_table/update", _wrap(update_tools_table))
register("catalogue/fetch/remote",   lambda p: _quiet(fetch_remote_to_local))
register("alias_model/list",          _wrap(op_alias_model_list))
register("alias_model/link",          _wrap(op_alias_model_link))
register("alias_model/unlink",        _wrap(op_alias_model_unlink))
register("alias_model/rebuild",       lambda p: _quiet(op_alias_model_rebuild, p))
register("tools/installed/list",     _wrap(get_installed_tools))
register("tools/install",            lambda p: _quiet(jobs.install_tool, p.get("ref"), None, _get_cat()))
register("tools/uninstall",          lambda p: _quiet(jobs.uninstall_tool, p.get("ref"), None, _get_cat()))
register("tools/install/all",        lambda p: _quiet(op_tools_install_all, p))
register("deps/check",               op_deps_check)
register("deps/install",             op_deps_install)
register("deps/install_target",      op_deps_install_target)
register("deps/check_manifest",      op_deps_check_manifest)
register("db/versions",              op_db_versions)
register("providers/list",           op_providers_list)
register("provider/endpoint/add",    op_provider_endpoint_add)
register("logs/read",                op_logs_read)
register("logs/write",               op_logs_write)
