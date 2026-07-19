"""Peuplement des provider_models depuis le cache litellm.

Le fichier model_prices_and_context_window.json (BerriAI/litellm) recense
des milliers de modeles pour de nombreux providers (azure, anthropic,
cohere, xai, perplexity, fireworks, mistral, gemini, ...). On s'en sert
pour PRE-REMPLIR les provider_models du catalogue meme sans cle API :
on cree le provider (si absent), le catalogue_models (si absent), puis le
lien provider_models avec le nom litellm, le context-window et le cout.

Comportement :
  - ne ecrase JAMAIS un cout/context deja present (merge additif).
  - provider litellm inconnu du catalogue -> cree (provider_type='cloud').
  - model_ref catalogue = dernier segment du nom litellm (ex:
    'openai/gpt-4o-mini' -> ref 'gpt-4o-mini', developer 'openai').
  - provider_model_name = nom litellm complet ('openai/gpt-4o-mini').

Usage:
    from modules.catalogue.populate import populate_provider_models
    populate_provider_models(CatalogueDB(), fetch_litellm_pricing())
"""

from typing import Any, Dict, Optional

from modules.catalogue.pricing import _normalize
from modules.catalogue.alias_discovery import (
    LITELLM_TARGET, _provider_heuristic)


def _ensure_provider(cat, ref: str) -> int:
    row = cat.conn.execute(
        "SELECT id FROM catalogue_providers WHERE ref=?", (ref,)).fetchone()
    if row:
        return row["id"]
    cur = cat.conn.execute(
        "INSERT INTO catalogue_providers (ref, name, provider_type) "
        "VALUES (?, ?, 'cloud')", (ref, ref))
    cat.conn.commit()
    return cur.lastrowid


def _ensure_model(cat, ref: str, developer: str) -> int:
    row = cat.conn.execute(
        "SELECT id FROM catalogue_models WHERE ref=?", (ref,)).fetchone()
    if row:
        return row["id"]
    cur = cat.conn.execute(
        "INSERT INTO catalogue_models (ref, name, developer, modality) "
        "VALUES (?, ?, ?, 'text')", (ref, ref, developer))
    cat.conn.commit()
    return cur.lastrowid


def populate_provider_models(cat, pricing: Dict[str, Any],
                             dry_run: bool = False,
                             only_existing_providers: bool = False
                             ) -> Dict[str, int]:
    """Pre-remplit provider_models depuis le cache litellm.

    `only_existing_providers` : si True, ignore les providers litellm absents
    du catalogue (ne les cree pas). Sinon les cree.
    Retourne {'providers_created', 'models_created', 'links_created',
              'links_updated', 'skipped'}.
    """
    # providers catalogue connus pour l'heuristique
    cat_providers = [r["ref"] for r in
                     cat.conn.execute("SELECT ref FROM catalogue_providers").fetchall()]
    declared_prov_alias = cat.alias_map(LITELLM_TARGET, "provider")

    stats = {"providers_created": 0, "models_created": 0,
             "links_created": 0, "links_updated": 0, "skipped": 0}
    seen_links = set()

    for key, entry in pricing.items():
        if "/" not in key:
            continue
        lp, lm = key.split("/", 1)
        canon_prov = declared_prov_alias.get(lp) or _provider_heuristic(lp, cat_providers)
        if canon_prov is None:
            stats["skipped"] += 1
            continue
        # provider existe-t-il dans le catalogue ?
        prow = cat.conn.execute(
            "SELECT id FROM catalogue_providers WHERE ref=?", (canon_prov,)).fetchone()
        if prow is None:
            if only_existing_providers:
                stats["skipped"] += 1
                continue
            if not dry_run:
                pid = _ensure_provider(cat, canon_prov)
            else:
                pid = -1
            stats["providers_created"] += 1
        else:
            pid = prow["id"]

        # model_ref catalogue = dernier segment du nom litellm
        model_ref = lm.split("/")[-1]
        developer = lp
        # modele existe-t-il ?
        mrow = cat.conn.execute(
            "SELECT id FROM catalogue_models WHERE ref=?", (model_ref,)).fetchone()
        if mrow is None:
            if not dry_run:
                mid = _ensure_model(cat, model_ref, developer)
            else:
                mid = -1
            stats["models_created"] += 1
        else:
            mid = mrow["id"]

        norm = _normalize(entry)
        link_key = (pid, mid)
        if link_key in seen_links:
            continue
        seen_links.add(link_key)

        existing = cat.conn.execute(
            "SELECT id, context_window_tokens, cost_per_input_token "
            "FROM provider_models WHERE provider_id=? AND model_id=?",
            (pid, mid)).fetchone() if not dry_run else None

        if existing:
            # mise a jour additive : ne remplit que les champs vides
            sets, vals = [], []
            if existing["context_window_tokens"] is None and norm["context_window_tokens"] is not None:
                sets.append("context_window_tokens=?")
                vals.append(norm["context_window_tokens"])
            if existing["cost_per_input_token"] is None and norm["cost_per_input_token"] is not None:
                sets.append("cost_per_input_token=?")
                vals.append(norm["cost_per_input_token"])
                sets.append("cost_per_output_token=?")
                vals.append(norm["cost_per_output_token"])
            if sets:
                vals.append(existing["id"])
                if not dry_run:
                    cat.conn.execute(
                        f"UPDATE provider_models SET {', '.join(sets)} WHERE id=?", vals)
                stats["links_updated"] += 1
        else:
            if not dry_run:
                cat.conn.execute(
                    """INSERT INTO provider_models
                       (provider_id, model_id, provider_model_name,
                        context_window_tokens, cost_per_input_token, cost_per_output_token)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (pid, mid, key, norm["context_window_tokens"],
                     norm["cost_per_input_token"], norm["cost_per_output_token"]))
            stats["links_created"] += 1

    if not dry_run:
        cat.conn.commit()
    return stats


# ── Peuplement NVIDIA depuis l'API officielle (/v1/models) ──────────────
# L'API OpenAI-compatible de NVIDIA (integrate.api.nvidia.com/v1/models)
# liste ~119 modeles reels (LLM hostes) absents du fichier litellm.
NVIDIA_API_MODELS = "https://integrate.api.nvidia.com/v1/models"


def populate_nvidia_from_api(cat, km, dry_run: bool = False,
                             timeout: int = 30) -> Dict[str, Any]:
    """Peuple les provider_models NVIDIA depuis l'API officielle.

    Necessite la cle NVIDIA (keyring). Pour chaque model_id API
    (ex: 'meta/llama-3.1-8b-instruct'), on cree le catalogue_models +
    provider_models NVIDIA, et un alias model (target=litellm) si le nom
    differe de notre ref catalogue. Retourne un resume.
    """
    import urllib.request
    import json

    stats: Dict[str, Any] = {"models_created": 0, "links_created": 0,
                             "links_updated": 0, "aliases": 0, "error": None}
    krow = km.get_key("nvidia") if km else None
    if not krow or not krow.get("api_key"):
        stats["error"] = "cle NVIDIA manquante"
        return stats
    api_base = krow.get("api_base") or "https://integrate.api.nvidia.com/v1"
    try:
        req = urllib.request.Request(
            f"{api_base.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {krow['api_key']}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        stats["error"] = str(e)[:120]
        return stats

    nvidia_prov = cat.conn.execute(
        "SELECT id FROM catalogue_providers WHERE ref='nvidia'").fetchone()
    if not nvidia_prov:
        if not dry_run:
            cur = cat.conn.execute(
                "INSERT INTO catalogue_providers (ref, name, provider_type) "
                "VALUES ('nvidia', 'NVIDIA', 'cloud')")
            cat.conn.commit()
            nvidia_pid = cur.lastrowid
        else:
            nvidia_pid = -1
    else:
        nvidia_pid = nvidia_prov["id"]

    seen = set()
    for m in data.get("data", []):
        mid_api = m.get("id")
        if not mid_api:
            continue
        if mid_api in seen:
            continue
        seen.add(mid_api)
        model_ref = mid_api.split("/")[-1]
        developer = mid_api.split("/")[0] if "/" in mid_api else "nvidia"

        mrow = cat.conn.execute(
            "SELECT id FROM catalogue_models WHERE ref=?", (model_ref,)).fetchone()
        if mrow is None:
            if not dry_run:
                cur = cat.conn.execute(
                    "INSERT INTO catalogue_models (ref, name, developer, modality) "
                    "VALUES (?, ?, ?, 'text')", (model_ref, model_ref, developer))
                mid = cur.lastrowid
            else:
                mid = -1
            stats["models_created"] += 1
        else:
            mid = mrow["id"]

        existing = (cat.conn.execute(
            "SELECT id FROM provider_models WHERE provider_id=? AND model_id=?",
            (nvidia_pid, mid)).fetchone() if not dry_run else None)
        if existing is None:
            if not dry_run:
                cat.conn.execute(
                    """INSERT INTO provider_models
                       (provider_id, model_id, provider_model_name)
                       VALUES (?, ?, ?)""", (nvidia_pid, mid, mid_api))
            stats["links_created"] += 1
        else:
            stats["links_updated"] += 1

        # alias model litellm -> notre ref si diverge
        if mid_api != model_ref:
            if not dry_run:
                rid = cat.add_alias("nvidia-official", "litellm", "model",
                                    mid_api, model_ref)
                if rid is not None:
                    stats["aliases"] += 1
            else:
                stats["aliases"] += 1

    if not dry_run:
        cat.conn.commit()
    return stats

