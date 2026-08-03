"""Catalogue HuggingFace : recherche de modèles GGUF + téléchargement.

Utilise l'API publique HuggingFace Hub (https://huggingface.co/api) :
  - recherche : /api/models?search=...&filter=gguf (tag + format de fichier)
  - téléchargement : huggingface_hub.hf_hub_download si installé, sinon
    téléchargement HTTP direct (fichiers .gguf épinglés par résolveur LFS)
  - progression : état en mémoire (thread-safe), pollable via une route.

Le dossier de destination est ~/.modelweaver/models/<repo>/ (fichiers GGUF).
L'association à un gestionnaire se fait ensuite :
  - llama.cpp : le fichier est déjà là → servable directement
  - ollama    : `ollama create <tag> -f <Modelfile>` (FROM chemin GGUF)
Un seul gestionnaire est choisi pour éviter les doublons de téléchargement.
"""

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.llm_manager.local_engines import _local_models_dir

HF_API = "https://huggingface.co/api/models"
_DL_STATE: Dict[str, Any] = {}
_DL_LOCK = threading.Lock()

GGUF_EXTENSIONS = (".gguf", ".ggml")
# Sous-dossiers de repos GGUF couramment utilisés
GGUF_DIRS = ("", "gguf", "GGUF", "models", "unsloth", "bartowski")


# ── Recherche ────────────────────────────────────────────────────────

def _is_gguf_model(model: Dict[str, Any]) -> bool:
    """Un modèle HF est GGUF si son tag l'indique ou s'il contient un .gguf."""
    tags = " ".join(model.get("tags") or []).lower()
    if "gguf" in tags or "ggml" in tags:
        return True
    for s in model.get("siblings", []):
        name = (s.get("rfilename") or "").lower()
        if name.endswith(GGUF_EXTENSIONS):
            return True
    return False


def hf_search(query: str = "", limit: int = 20) -> Dict[str, Any]:
    """Recherche de modèles GGUF sur HuggingFace.

    Filtre côté serveur `filter=gguf` (fiable) + validation client sur le
    tag. L'API ne renvoyant pas les fichiers des repos GGUF (LFS), la
    liste des fichiers et la taille sont résolues à la demande, au
    moment du téléchargement (voir `_pick_gguf_file`).
    """
    params = [f"limit={int(limit)}", "filter=gguf", "siblings=true"]
    if query:
        params.append(f"search={urllib.parse.quote(query)}")
    url = HF_API + "?" + "&".join(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"status": "error", "error": f"API HF injoignable: {e}"}
    results: List[Dict[str, Any]] = []
    for m in raw or []:
        if not _is_gguf_model(m):
            continue
        results.append({
            "id": m.get("id"),
            "modelId": m.get("modelId") or m.get("id"),
            "author": (m.get("id") or "/").split("/")[0] if m.get("id") else "",
            "downloads": m.get("downloads") or 0,
            "likes": m.get("likes") or 0,
            "pipeline_tag": m.get("pipeline_tag"),
            "gguf_files": [],  # résolus à la demande (API ne les liste pas)
            "total_size_bytes": 0,
            "total_size_gb": None,
        })
    results.sort(key=lambda r: r["downloads"], reverse=True)
    return {"status": "ok", "count": len(results), "results": results}


# ── Téléchargement ───────────────────────────────────────────────────

def _pick_gguf_file(repo_id: str) -> Optional[str]:
    """Choisit le fichier GGUF à télécharger (repo_info si dispo, sinon
    l'API models pour lister les siblings)."""
    url = f"{HF_API}/{repo_id.replace('/', '/')}"
    try:
        req = urllib.request.Request(
            f"https://huggingface.co/api/models/{repo_id}",
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    gguv = [s.get("rfilename", "") for s in data.get("siblings", [])
            if (s.get("rfilename") or "").lower().endswith(GGUF_EXTENSIONS)]
    if not gguv:
        return None
    # Préférer un fichier le plus petit ? Non : Q4_K_M si possible
    for f in gguv:
        low = f.lower()
        if "q4_k_m" in low or "q4km" in low or "q4_0" in low:
            return f
    return gguv[0]


def _resolve_lfs_url(repo_id: str, filename: str) -> Optional[str]:
    """Résout l'URL directe d'un fichier (résolveur LFS huggingface.co)."""
    return f"https://huggingface.co/{repo_id}/resolve/main/{filename}"


def _download_http(repo_id: str, filename: str, dest: Path,
                   state_key: str) -> bool:
    """Téléchargement HTTP direct avec progression (résolveur LFS)."""
    url = _resolve_lfs_url(repo_id, filename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "modelweaver/0.8"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    with _DL_LOCK:
                        _DL_STATE[state_key]["bytes"] = got
                        _DL_STATE[state_key]["total"] = total
        os.replace(tmp, dest)
        return True
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        with _DL_LOCK:
            _DL_STATE[state_key]["error"] = str(e)
        return False


def hf_download(repo_id: str, filename: Optional[str] = None) -> Dict[str, Any]:
    """Lance un téléchargement GGUF depuis HF (dans ~/.modelweaver/models/).

    État suivi en mémoire (clé de téléchargement unique) — pollable via
    hf_download_status. Best-effort sur huggingface_hub si dispo.
    """
    if not repo_id:
        return {"status": "error", "error": "repo_id requis"}
    if filename is None:
        filename = _pick_gguf_file(repo_id)
        if not filename:
            return {"status": "error",
                    "error": f"aucun fichier GGUF dans {repo_id}"}
    safe_repo = repo_id.replace("/", "__")
    key = f"{safe_repo}:{filename}"
    with _DL_LOCK:
        if key in _DL_STATE and _DL_STATE[key].get("done"):
            return {"status": "ok", "download_id": key,
                    "already_done": True, "path": str(
                        _local_models_dir() / safe_repo / filename)}
        _DL_STATE[key] = {"repo": repo_id, "filename": filename,
                          "bytes": 0, "total": 0, "done": False,
                          "started": time.time(), "error": None,
                          "thread": None}
    dest = _local_models_dir() / safe_repo / filename

    def _work():
        # huggingface_hub si dispo (gère LFS + resume)
        try:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(repo_id=repo_id, filename=filename,
                                   local_dir=str(dest.parent))
            with _DL_LOCK:
                st = _DL_STATE[key]
                st["done"] = True
                st["path"] = path
                try:
                    st["total"] = Path(path).stat().st_size
                except Exception:
                    pass
            return
        except ImportError:
            pass
        except Exception as e:
            # chute sur HTTP direct si huggingface_hub échoue
            pass
        ok = _download_http(repo_id, filename, dest, key)
        with _DL_LOCK:
            _DL_STATE[key]["done"] = ok

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    with _DL_LOCK:
        _DL_STATE[key]["thread"] = t
    return {"status": "ok", "download_id": key, "repo": repo_id,
            "filename": filename, "path": str(dest)}


def hf_download_status(download_id: str) -> Dict[str, Any]:
    """État d'un téléchargement en cours."""
    with _DL_LOCK:
        st = _DL_STATE.get(download_id)
        if not st:
            return {"status": "error", "error": "téléchargement inconnu"}
        return {k: st[k] for k in ("repo", "filename", "bytes", "total",
                                   "done", "error", "started")
                if k in st}


def hf_downloads_all() -> Dict[str, Any]:
    """Liste tous les téléchargements (actifs + terminés de la session)."""
    with _DL_LOCK:
        items = [{"download_id": k,
                  "repo": v.get("repo"), "filename": v.get("filename"),
                  "bytes": v.get("bytes", 0), "total": v.get("total", 0),
                  "done": v.get("done", False), "error": v.get("error"),
                  "path": v.get("path")}
                 for k, v in _DL_STATE.items()]
    return {"status": "ok", "count": len(items), "downloads": items}


# ── Association gestionnaire ─────────────────────────────────────────

def associate_model(repo_id: str, filename: str, engine: str,
                    tag: Optional[str] = None) -> Dict[str, Any]:
    """Associe un GGUF téléchargé à UN gestionnaire (pas de doublon).

    - llama.cpp : rien à faire (le fichier est déjà servable) → ok
    - ollama    : `ollama create <tag> -f <Modelfile>` (FROM chemin GGUF)
    """
    path = _local_models_dir() / repo_id.replace("/", "__") / filename
    if not path.exists():
        # chercher ailleurs (huggingface_hub a pu placer ailleurs)
        alt = _local_models_dir() / (repo_id.replace("/", "__")) / filename
        if alt.exists():
            path = alt
        else:
            for f in (_local_models_dir() / repo_id.replace("/", "__")).rglob("*.gguf"):
                path = f
                break
    if not path.exists():
        return {"status": "error",
                "error": f"fichier introuvable: {path}"}
    if engine == "llamacpp":
        return {"status": "ok", "engine": engine, "path": str(path),
                "note": "fichier déjà servable par llama.cpp"}
    if engine == "ollama":
        tag = tag or repo_id.split("/")[-1]
        modelfile = f"FROM {path}"
        mf = Path(_local_models_dir()) / f"Modelfile.{tag}"
        try:
            mf.write_text(modelfile)
            res = subprocess.run(["ollama", "create", tag, "-f", str(mf)],
                                 capture_output=True, text=True, timeout=600)
        except FileNotFoundError:
            return {"status": "error",
                    "error": "ollama introuvable — installez Ollama"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
        if res.returncode == 0:
            return {"status": "ok", "engine": engine, "tag": tag,
                    "path": str(path)}
        return {"status": "error",
                "error": f"ollama create échoué: {res.stderr.strip()[:300]}"}
    return {"status": "error", "error": f"gestionnaire inconnu: {engine}"}


def list_local_models() -> List[Dict[str, Any]]:
    """Modèles GGUF téléchargés via le catalogue HF (métadonnées)."""
    out = []
    base = _local_models_dir()
    if not base.exists():
        return out
    for f in sorted(base.rglob("*.gguf")):
        try:
            size_gb = round(f.stat().st_size / (1024 ** 3), 2)
        except Exception:
            size_gb = None
        rel = f.relative_to(base)
        out.append({
            "repo": rel.parts[0].replace("__", "/") if len(rel.parts) > 1 else "",
            "filename": f.name,
            "path": str(f),
            "size_gb": size_gb,
        })
    return out
