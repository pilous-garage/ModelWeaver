"""Synchronisation des tarifs / context-window des modeles dans le catalogue.

Sources (multi, pluggables) :
  1. GitHub communautaire : BerriAI/litellm model_prices_and_context_window.json
     (des milliers de modeles, input/output cost per token, context, max output).
  2. (futur) Pages pricing officielles par provider — scrapees automatiquement,
     puis lues par un agent LLM specialise (annonce).
  3. (futur) Endpoints liste-modeles deja en place (sans tarif, complement).

Pour l'instant : methode automatisee via la source GitHub. Le merge remplit
provider_models.cost_per_input_token / cost_per_output_token /
context_window_tokens / max_output_tokens. Tout est rempli (gratuit + payant) ;
les modeles a cout nul (= 0) sont consideres free-tier.

Utilisation :
    from modules.catalogue.pricing import fetch_litellm_pricing, merge_pricing
    pricing = fetch_litellm_pricing()
    merge_pricing(CatalogueDB(), pricing)
"""

import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = REPO_ROOT / ".modelweaver" / "cache" / "pricing"

LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# Réconciliation des noms de provider litellm -> ref catalogue_model
# (litellm utilise parfois des noms differents de notre catalogue).
LITELLM_PROVIDER_ALIASES = {
    "gemini": "google",
    "google": "google",
    "nvidia_nim": "nvidia",
    "vertex_ai": "google-vertex",
    "azure_ai": "azure",
}


# ── Récupération (source communautaire GitHub) ──────────────────────────
def fetch_litellm_pricing(force: bool = False) -> Dict[str, Any]:
    """Telecharge (avec cache disque) le JSON de tarifs litellm.

    Retourne un dict { "provider/model": {input_cost_per_token, ...}, ... }.
    Le cache est reutilise sauf `force` ou cache absent/illisible.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "litellm_pricing.json"
    if not force and cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    req = urllib.request.Request(
        LITELLM_PRICING_URL, headers={"User-Agent": "modelweaver-sync/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    try:
        cache.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass
    return data


# ── Normalisation d'une entree litellm -> champs catalogue ──────────────
def _normalize(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Extrait les champs catalogue utiles depuis une entree litellm."""
    inp = entry.get("input_cost_per_token")
    out = entry.get("output_cost_per_token")
    ctx = entry.get("max_input_tokens") or entry.get("max_tokens")
    max_out = entry.get("max_output_tokens") or entry.get("max_tokens")
    return {
        "cost_per_input_token": _to_str(inp),
        "cost_per_output_token": _to_str(out),
        "context_window_tokens": int(ctx) if ctx else None,
        "max_output_tokens": int(max_out) if max_out else None,
    }


def _to_str(v) -> Optional[str]:
    """litellm stocke les couts en float (ex: 5e-08). On garde la notation
    scientifique en texte pour eviter la perte de precision."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # 0.0 => free-tier, on garde '0.0' (utile pour filtrer plus tard)
    return repr(f)


# ── Merge dans le catalogue ─────────────────────────────────────────────
def merge_pricing(cat, pricing: Dict[str, Any], dry_run: bool = False,
                  only_free_tier: bool = False) -> Dict[str, int]:
    """Applique les tarifs a provider_models via link_provider.

    Apparie "provider/model" (cle litellm) avec
    (catalogue_providers.ref = provider) + provider_models.provider_model_name
    = model (ou fin de ref). Met a jour cost/context/max_output.

    Retourne un dict de compteurs : matched, updated, free_tier, skipped.
    """
    rows = cat.conn.execute("""
        SELECT pm.id, pm.provider_model_name, pm.provider_id, pm.model_id,
               p.ref AS provider_ref, m.ref AS model_ref,
               pm.cost_per_input_token, pm.cost_per_output_token,
               pm.context_window_tokens
        FROM provider_models pm
        JOIN catalogue_providers p ON p.id = pm.provider_id
        JOIN catalogue_models m ON m.id = pm.model_id
    """).fetchall()

    by_name = {}
    by_suffix = {}        # (provider_ref, model_ref dernier segment)
    by_pmn_suffix = {}    # (provider_ref, provider_model_name dernier segment)
    for r in rows:
        by_name.setdefault((r["provider_ref"], r["provider_model_name"]), []).append(r)
        suffix = r["model_ref"].split("/")[-1]
        by_suffix.setdefault((r["provider_ref"], suffix), []).append(r)
        pmn_suffix = r["provider_model_name"].split("/")[-1]
        by_pmn_suffix.setdefault((r["provider_ref"], pmn_suffix), []).append(r)

    stats = {"matched": 0, "updated": 0, "free_tier": 0, "skipped": 0}
    updates = []

    for key, entry in pricing.items():
        if "/" not in key:
            continue
        prov, model = key.split("/", 1)
        # reconcilie le nom de provider litellm -> ref catalogue
        prov = LITELLM_PROVIDER_ALIASES.get(prov, prov)
        norm = _normalize(entry)
        is_free = (
            norm["cost_per_input_token"] in (None, "0.0")
            and norm["cost_per_output_token"] in (None, "0.0")
        )
        if only_free_tier and not is_free:
            continue

        m_suffix = model.split("/")[-1]
        candidates = (
            by_name.get((prov, model))
            or by_pmn_suffix.get((prov, m_suffix))
            or by_suffix.get((prov, m_suffix))
        )
        if not candidates:
            stats["skipped"] += 1
            continue
        stats["matched"] += 1
        if is_free:
            stats["free_tier"] += 1
        for r in candidates:
            extra = {}
            if r["cost_per_input_token"] is None and norm["cost_per_input_token"] is not None:
                extra["cost_per_input_token"] = norm["cost_per_input_token"]
            if r["cost_per_output_token"] is None and norm["cost_per_output_token"] is not None:
                extra["cost_per_output_token"] = norm["cost_per_output_token"]
            if r["context_window_tokens"] is None and norm["context_window_tokens"] is not None:
                extra["context_window_tokens"] = norm["context_window_tokens"]
            if norm["max_output_tokens"] is not None:
                extra["max_output_tokens"] = norm["max_output_tokens"]
            if extra:
                updates.append((r["id"], extra))
                stats["updated"] += 1

    if not dry_run:
        for pm_id, extra in updates:
            sets, vals = [], []
            for col in ("cost_per_input_token", "cost_per_output_token",
                        "context_window_tokens", "max_output_tokens"):
                if col in extra:
                    sets.append(f"{col}=?")
                    vals.append(extra[col])
            if sets:
                vals.append(pm_id)
                cat.conn.execute(
                    f"UPDATE provider_models SET {', '.join(sets)} "
                    f"WHERE id=?", vals)
        cat.conn.commit()

    return stats


# ── Registre de sources (futur : pages officielles, agents LLM) ──────────
PRICING_SOURCES = {
    "litellm_github": fetch_litellm_pricing,
}


def sync_all(cat, force: bool = False, dry_run: bool = False,
             only_free_tier: bool = False) -> Dict[str, Any]:
    """Lance toutes les sources de tarifs et merge. Retourne un resume."""
    report = {}
    for name, fetcher in PRICING_SOURCES.items():
        try:
            data = fetcher()
            if force and name == "litellm_github":
                data = fetch_litellm_pricing(force=True)
            stats = merge_pricing(cat, data, dry_run=dry_run, only_free_tier=only_free_tier)
            report[name] = stats
        except Exception as e:
            report[name] = {"error": str(e)}
    return report
