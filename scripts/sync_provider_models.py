"""Synchronise les modeles accessibles par fournisseur dans le CatalogueDB.

Pour chaque fournisseur disposant d'une cle (keyring ou env), on interroge
l'endpoint « list models » du fournisseur et on peuple :
  - catalogue_models        (modele pur, une fois)
  - provider_models         (lien fournisseur -> modele) avec `available=1`
                            pour les modeles reellement joignables.

Les modeles deja presents mais NON revus lors de la synchro sont marques
`available=0` (on ne les supprime pas : ils peuvent revenir, ou etre
utilises hors-ligne). Le champ `available` sert ensuite a l'Orchestrateur
pour n'attribuer QUE des modeles accessibles.

Fournisseurs supportees (endpoint liste-modeles) :
  - google/gemini : GET https://generativelanguage.googleapis.com/v1beta/models
  - openai         : GET https://api.openai.com/v1/models
  - mistral        : GET https://api.mistral.ai/v1/models
  - groq           : GET https://api.groq.com/openai/v1/models
  - ollama (local) : GET http://127.0.0.1:11434/api/tags

Usage:
  python3 scripts/sync_provider_models.py [--dry-run] [--provider groq]
"""

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.sql.db import CatalogueDB, ModelWeaverDB
from modules.key_manager.key_manager import KeyManager


# ── endpoints liste-modeles par fournisseur ──
# (api_base, path, extractor) ; extractor renvoie la liste des noms de modeles
def _http_get_json(url, headers=None, timeout=20):
    h = {"User-Agent": "modelweaver-sync/1.0", "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# Suffixes/noms a EXCLURE : ce ne sont pas des LLM de chat texte standard
# (image, video, audio/tts, embeddings, previews specials, agents integres).
_EXCLUDE_HINTS = (
    "image", "imagen", "veo", "lyria", "tts", "embed", "nano-banana",
    "antigravity", "computer-use", "robotics", "deep-research", "omni",
    "preview-tts", "flash-image", "pro-image", "vision-preview",
)


def _is_text_chat_model(name: str) -> bool:
    n = name.lower()
    return not any(h in n for h in _EXCLUDE_HINTS)


def _gemini_list(key):
    data = _http_get_json(
        "https://generativelanguage.googleapis.com/v1beta/models?key=" + key,
        timeout=25,
    )
    out = []
    for m in data.get("models", []):
        name = m["name"].split("/")[-1]
        if not _is_text_chat_model(name):
            continue
        acts = m.get("supportedGenerationMethods", [])
        # on ne garde que les modeles de generation de texte/contour
        if any(a in acts for a in ("generateContent", "generateContentText")):
            out.append({
                "name": name,
                "developer": "google",
                "context_window_tokens": int(m.get("inputTokenLimit") or 0),
                "modality": "text",
            })
    return out


def _openai_style_list(base_url, key, developer):
    data = _http_get_json(base_url + "/models",
                          headers={"Authorization": f"Bearer {key}"})
    out = []
    for m in data.get("data", []):
        mid = m["id"]
        if not _is_text_chat_model(mid):
            continue
        out.append({
            "name": mid,
            "developer": developer,
            "context_window_tokens": None,
            "modality": "text",
        })
    return out


def _ollama_list():
    try:
        data = _http_get_json("http://127.0.0.1:11434/api/tags", timeout=5)
    except Exception:
        return []
    out = []
    for m in data.get("models", []):
        out.append({
            "name": m["name"],
            "developer": "ollama",
            "context_window_tokens": None,
            "modality": "text",
        })
    return out


# Mapping fournisseur -> (fetch_fn, api_base)
def _fetcher_for(provider, key, api_base):
    if provider in ("google", "gemini"):
        return lambda: _gemini_list(key)
    if provider == "openai":
        return lambda: _openai_style_list(api_base or "https://api.openai.com/v1", key, "openai")
    if provider == "mistral":
        return lambda: _openai_style_list(api_base or "https://api.mistral.ai/v1", key, "mistral")
    if provider == "groq":
        return lambda: _openai_style_list(api_base or "https://api.groq.com/openai/v1", key, "groq")
    if provider == "nvidia":
        return lambda: _openai_style_list(api_base or "https://integrate.api.nvidia.com/v1", key, "nvidia")
    if provider == "ollama":
        return _ollama_list
    return None


def _resolve_key(km, provider, api_base):
    info = km.get_key(provider) if km else None
    if info:
        return info.get("api_key"), info.get("api_base") or api_base
    env_key = os.environ.get(f"{provider.upper()}_API_KEY")
    if env_key:
        return env_key, api_base
    return None, api_base


def sync_provider(cat, km, provider, dry_run=False, ping=False):
    key, api_base = _resolve_key(km, provider, None)
    if provider != "ollama" and not key:
        print(f"  [{provider}] skip (no key)")
        return 0
    fetcher = _fetcher_for(provider, key, api_base)
    if fetcher is None:
        print(f"  [{provider}] skip (no fetcher)")
        return 0

    try:
        models = fetcher()
    except Exception as e:
        print(f"  [{provider}] ERROR fetching: {str(e)[:80]}")
        return 0

    if not models:
        print(f"  [{provider}] no models returned")
        return 0

    # marque tous les anciens liens de ce fournisseur comme non revus
    conn = cat.conn
    cur = conn.execute("SELECT id FROM catalogue_providers WHERE ref=?", (provider,))
    row = cur.fetchone()
    if not row:
        conn.execute(
            "INSERT INTO catalogue_providers (ref, name, provider_type) VALUES (?, ?, ?)",
            (provider, provider, "local" if provider == "ollama" else "cloud"),
        )
        pid = conn.execute("SELECT id FROM catalogue_providers WHERE ref=?", (provider,)).fetchone()[0]
    else:
        pid = row["id"]

    bridge = None
    if ping and not dry_run:
        from modules.llm_manager.litellm_bridge import LiteLLMBridge
        bridge = LiteLLMBridge(cat=cat, km=km)

    seen = 0
    for md in models:
        mname = md["name"]
        # entree modele pur (idempotent via ref = developer/name)
        mref = f"{md['developer']}/{mname}"
        cur = conn.execute("SELECT id FROM catalogue_models WHERE ref=?", (mref,))
        r = cur.fetchone()
        if r:
            mid = r["id"]
        else:
            conn.execute(
                "INSERT INTO catalogue_models (ref, name, developer, modality) "
                "VALUES (?, ?, ?, ?)",
                (mref, mname, md["developer"], md.get("modality", "text")),
            )
            mid = conn.execute("SELECT id FROM catalogue_models WHERE ref=?", (mref,)).fetchone()[0]
        extra = {}
        if md.get("context_window_tokens"):
            extra["context_window_tokens"] = md["context_window_tokens"]
        cw = extra.get("context_window_tokens")

        # ping reel : on ne marque available=1 que si le modele repond
        available = 1
        if ping and bridge is not None:
            try:
                bridge.chat(provider_ref=provider, model_ref=mname,
                            messages=[{"role": "user", "content": "hi"}],
                            max_tokens=1)
            except Exception:
                available = 0
        if not dry_run:
            conn.execute(
                "INSERT OR REPLACE INTO provider_models "
                "(provider_id, model_id, provider_model_name, context_window_tokens, "
                " available, updated_at) VALUES (?, ?, ?, ?, ?, strftime('%s','now'))",
                (pid, mid, mname, cw, available),
            )
        if available:
            seen += 1

    print(f"  [{provider}] {seen} models marked available")
    return seen


def get_available_models(cat: CatalogueDB, provider: Optional[str] = None) -> List[Dict[str, str]]:
    """Retourne les (provider, model) reellement joignables (available=1).

    Utilise par l'Orchestrateur pour n'attribuer QUE des modeles accessibles.
    """
    if provider:
        rows = cat.conn.execute(
            "SELECT cp.ref AS provider, pm.provider_model_name AS model "
            "FROM provider_models pm "
            "JOIN catalogue_providers cp ON cp.id = pm.provider_id "
            "WHERE pm.available = 1 AND cp.ref = ? "
            "ORDER BY pm.provider_model_name",
            (provider,),
        ).fetchall()
    else:
        rows = cat.conn.execute(
            "SELECT cp.ref AS provider, pm.provider_model_name AS model "
            "FROM provider_models pm "
            "JOIN catalogue_providers cp ON cp.id = pm.provider_id "
            "WHERE pm.available = 1 "
            "ORDER BY cp.ref, pm.provider_model_name",
        ).fetchall()
    return [{"provider": r["provider"], "model": r["model"]} for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ping", action="store_true",
                    help="verifie la joignabilite reelle de chaque modele (chat test)")
    ap.add_argument("--provider", help="ne synchroniser qu'un fournisseur")
    args = ap.parse_args()

    cat = CatalogueDB()
    km = KeyManager(ModelWeaverDB())
    try:
        km.load()
    except Exception:
        km = None

    providers = [args.provider] if args.provider else [
        "groq", "openai", "mistral", "google", "nvidia", "ollama"
    ]
    # Reset global : tout modele non reconfirme lors de cette synchro passe
    # a available=0 (on ne supprime pas les entrees, on marque non joignable).
    if not args.dry_run and not args.provider:
        cat.conn.execute("UPDATE provider_models SET available=0")
    total = 0
    print(f"== sync_provider_models (dry_run={args.dry_run}, ping={args.ping}) ==")
    for p in providers:
        total += sync_provider(cat, km, p, dry_run=args.dry_run, ping=args.ping)
    if not args.dry_run:
        cat.conn.commit()
    print(f"== done: {total} model links available ==")


if __name__ == "__main__":
    main()
