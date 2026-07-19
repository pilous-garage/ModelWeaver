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

# key_ref sentinelle pour les modeles accessibles SANS cle requise
# (ex: ollama local, endpoints publics). Distinct d'une vraie ref de cle.
KEY_REF_PUBLIC = ""


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

    # endpoints de ce provider (au moins 1 basique par defaut)
    endpoints = conn.execute(
        "SELECT endpoint_id, endpoint_url FROM provider_endpoints WHERE provider_id=?",
        (pid,)).fetchall()
    if not endpoints:
        print(f"  [{provider}] no endpoint defined")
        return 0

    # cles de ce provider (ref).
    # - provider sans cle (ollama local, ou cloud sans key definie) :
    #   on garde les modeles accessibles SANS cle -> KEY_REF_PUBLIC.
    # - provider avec cle : on stocke la vraie ref de cle.
    # On ne laisse JAMAIS key_refs vide (sinon aucune ligne key_endpoint_models
    # n'est creee). La distinction cle/sans-cle se fait via key_ref == '' .
    key_refs = []
    if provider == "ollama":
        key_refs = [KEY_REF_PUBLIC]
    else:
        krow = km.get_key(provider) if km else None
        if krow and krow.get("ref"):
            key_refs = [krow["ref"]]
        else:
            # pas de cle : modeles accessibles sans cle (endpoint public,
            # ou liste-modele ouverte). On les garde en KEY_REF_PUBLIC.
            key_refs = [KEY_REF_PUBLIC]

    bridge = None
    if ping and not dry_run:
        from modules.llm_manager.litellm_bridge import LiteLLMBridge
        bridge = LiteLLMBridge(cat=cat, km=km)

    total = 0
    for ep in endpoints:
        eid, ep_url = ep["endpoint_id"], ep["endpoint_url"]
        for kref in key_refs:
            # reset declared pour (endpoint, key) avant ce refresh
            if not dry_run:
                conn.execute(
                    "UPDATE key_endpoint_models SET declared=0 "
                    "WHERE endpoint_id=? AND key_ref=?",
                    (eid, kref))
            seen = 0
            for md in models:
                mname = md["name"]
                mref = f"{md['developer']}/{mname}"
                r = conn.execute("SELECT id FROM catalogue_models WHERE ref=?",
                                 (mref,)).fetchone()
                if r:
                    mid = r["id"]
                else:
                    conn.execute(
                        "INSERT INTO catalogue_models (ref, name, developer, modality) "
                        "VALUES (?, ?, ?, ?)",
                        (mref, mname, md["developer"], md.get("modality", "text")))
                    mid = conn.execute("SELECT id FROM catalogue_models WHERE ref=?",
                                       (mref,)).fetchone()[0]
                cw = md.get("context_window_tokens")
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
                        "INSERT OR REPLACE INTO key_endpoint_models "
                        "(provider_id, endpoint_id, key_ref, model_id, "
                        " provider_model_name, declared, available, last_checked_at) "
                        "VALUES (?,?,?,?,?,1,?,strftime('%s','now'))",
                        (pid, eid, kref, mid, mname, available))
                if available:
                    seen += 1
            key_label = "public" if kref == KEY_REF_PUBLIC else kref
            print(f"  [{provider}] endpoint#{eid} key={key_label}: {seen} models declared")
            total += seen
    return total


def get_available_models(cat: CatalogueDB, provider: Optional[str] = None) -> List[Dict[str, str]]:
    """Retourne les (provider, model) reellement joignables (available=1).

    Utilise par l'Orchestrateur pour n'attribuer QUE des modeles accessibles.
    """
    if provider:
        rows = cat.conn.execute(
            "SELECT cp.ref AS provider, kem.provider_model_name AS model "
            "FROM key_endpoint_models kem "
            "JOIN catalogue_providers cp ON cp.id = kem.provider_id "
            "WHERE kem.available = 1 AND kem.declared = 1 AND cp.ref = ? "
            "ORDER BY kem.provider_model_name",
            (provider,),
        ).fetchall()
    else:
        rows = cat.conn.execute(
            "SELECT cp.ref AS provider, kem.provider_model_name AS model "
            "FROM key_endpoint_models kem "
            "JOIN catalogue_providers cp ON cp.id = kem.provider_id "
            "WHERE kem.available = 1 AND kem.declared = 1 "
            "ORDER BY cp.ref, kem.provider_model_name",
        ).fetchall()
    return [{"provider": r["provider"], "model": r["model"]} for r in rows]


def get_models_by_access(cat: CatalogueDB, provider: Optional[str] = None
                         ) -> Dict[str, List[Dict[str, str]]]:
    """Classe les modeles joignables selon l'acces requis.

    Retourne {'with_key': [...], 'public': [...]} où :
      - 'with_key' : modeles necessitant une cle (key_ref != '' , available=1).
      - 'public'   : modeles accessibles SANS cle (key_ref = '' , available=1).
    Chaque entree = {provider, model, key_ref}.
    """
    clause = "WHERE kem.available = 1 AND kem.declared = 1"
    params = []
    if provider:
        clause += " AND cp.ref = ?"
        params.append(provider)
    rows = cat.conn.execute(f"""
        SELECT cp.ref AS provider, kem.provider_model_name AS model,
               kem.key_ref AS key_ref
        FROM key_endpoint_models kem
        JOIN catalogue_providers cp ON cp.id = kem.provider_id
        {clause}
        ORDER BY cp.ref, kem.provider_model_name
    """, params).fetchall()
    out = {"with_key": [], "public": []}
    for r in rows:
        entry = {"provider": r["provider"], "model": r["model"],
                 "key_ref": r["key_ref"]}
        if r["key_ref"] == KEY_REF_PUBLIC:
            out["public"].append(entry)
        else:
            out["with_key"].append(entry)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ping", action="store_true",
                    help="verifie la joignabilite reelle de chaque modele (chat test)")
    ap.add_argument("--provider", help="ne synchroniser qu'un fournisseur")
    ap.add_argument("--pricing", action="store_true",
                    help="synchronise aussi les tarifs (source GitHub litellm)")
    ap.add_argument("--only-free", action="store_true",
                    help="avec --pricing : ne remplit que les modeles free-tier")
    ap.add_argument("--force-pricing", action="store_true",
                    help="avec --pricing : re-telecharge la source sans cache")
    ap.add_argument("--show-aliases", action="store_true",
                    help="affiche les alias de reconciliation (catalogue_aliases)")
    ap.add_argument("--show-access", action="store_true",
                    help="classe les modeles joignables : avec cle / sans cle (public)")
    ap.add_argument("--discover-aliases", action="store_true",
                    help="decouvre automatiquement les alias provider/model "
                         "litellm -> catalogue (depot GitHub litellm) et les insere")
    ap.add_argument("--populate", action="store_true",
                    help="pre-remplit provider_models depuis le cache litellm "
                         "(providers non peuples : azure, anthropic, cohere...)")
    ap.add_argument("--populate-nvidia", action="store_true",
                    help="peuple les provider_models NVIDIA depuis l'API "
                         "officielle /v1/models (necessite cle NVIDIA)")
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
    # a declared=0 (on ne supprime pas les entrees, on marque non joignable).
    if not args.dry_run and not args.provider:
        cat.conn.execute("UPDATE key_endpoint_models SET declared=0")
    total = 0
    print(f"== refresh_model_access (dry_run={args.dry_run}, ping={args.ping}) ==")
    for p in providers:
        total += sync_provider(cat, km, p, dry_run=args.dry_run, ping=args.ping)
    if not args.dry_run:
        cat.conn.commit()
    print(f"== done: {total} model links declared/available ==")

    if args.pricing:
        from modules.catalogue.pricing import sync_all
        print(f"== sync_pricing (force={args.force_pricing}, "
              f"only_free={args.only_free}) ==")
        report = sync_all(cat, force=args.force_pricing, dry_run=args.dry_run,
                          only_free_tier=args.only_free)
        for src, stats in report.items():
            print(f"  [{src}] {stats}")

    if args.show_aliases:
        print("== catalogue_aliases ==")
        for a in cat.list_aliases():
            print(f"  [{a['source']}] target={a['target']} {a['scope']}: "
                  f"{a['alias']} -> {a['canonical_ref']} (prio {a['priority']})")

    if args.discover_aliases:
        from modules.catalogue.alias_discovery import (
            discover_litellm_aliases, discover_nvidia_official_aliases)
        from modules.catalogue.pricing import fetch_litellm_pricing
        print("== discover_aliases (target=litellm) ==")
        lit = fetch_litellm_pricing()
        stats = discover_litellm_aliases(cat, lit, dry_run=args.dry_run)
        print(f"  [github-litellm-list] {stats}")
        nv = discover_nvidia_official_aliases(cat, dry_run=args.dry_run)
        print(f"  [nvidia-official] {nv}")

    if args.populate:
        from modules.catalogue.populate import populate_provider_models
        from modules.catalogue.pricing import fetch_litellm_pricing
        print("== populate provider_models (source litellm) ==")
        lit = fetch_litellm_pricing()
        stats = populate_provider_models(cat, lit, dry_run=args.dry_run)
        print(f"  {stats}")

    if args.populate_nvidia:
        from modules.catalogue.populate import populate_nvidia_from_api
        print("== populate NVIDIA (API officielle /v1/models) ==")
        stats = populate_nvidia_from_api(cat, km, dry_run=args.dry_run)
        print(f"  {stats}")

    if args.show_access:
        by = get_models_by_access(cat)
        print(f"== acces modeles (joignables) ==")
        print(f"  [avec cle] {len(by['with_key'])} modeles :")
        for e in by["with_key"][:20]:
            print(f"    {e['provider']}/{e['model']} (key={e['key_ref']})")
        if len(by["with_key"]) > 20:
            print(f"    ... +{len(by['with_key']) - 20} autres")
        print(f"  [sans cle / public] {len(by['public'])} modeles :")
        for e in by["public"][:20]:
            print(f"    {e['provider']}/{e['model']}")
        if len(by["public"]) > 20:
            print(f"    ... +{len(by['public']) - 20} autres")


if __name__ == "__main__":
    main()
