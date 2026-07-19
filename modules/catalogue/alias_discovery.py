"""Decouverte automatique d'alias provider/model depuis le cache litellm.

Le fichier model_prices_and_context_window.json (BerriAI/litellm, sur GitHub)
est une source de verite pour les NOMS de modeles tels qu'ecrits par litellm.
On en deduit les alias (target='litellm') vers les refs canoniques du
catalogue, sans les hardcoder : on croise chaque cle "provider/model" litellm
avec les provider_models du catalogue.

Strategie de matching (provider puis model) :
  - provider litellm -> provider catalogue via les alias provider DEJA declares
    dans catalogue_aliases (target=litellm), sinon par heuristique de nom
    (prefixe avant '/', ou egalite insensible a '-'/'_').
  - model litellm -> model_ref catalogue par :
      1. egalite exacte (apres strip du prefixe provider litellm)
      2. suffixe (dernier segment de ref)
      3. egalite insensible cas/separateurs
  - on n'insere un alias QUE si litellm != catalogue (declaration passive),
    et si le match est NON-ambigu (1 seul candidat).

Cible : peupler catalogue_aliases pour tous les providers litellm qui
possident des modeles matchables dans notre catalogue (groq, openai, google,
mistral, azure, deepseek, openrouter, nvidia si donnees presente, ...).

Usage:
    from modules.catalogue.alias_discovery import discover_litellm_aliases
    discover_litellm_aliases(CatalogueDB(), dry_run=False)
"""

from typing import Any, Dict, List, Optional

LITELLM_TARGET = "litellm"
LITELLM_SOURCE = "github-litellm-list"


def _norm_name(s: str) -> str:
    """Normalise un nom pour la comparaison insensible (minuscules,
    separateurs unifies en '_')."""
    return s.lower().replace("/", "_").replace("-", "_").replace(".", "_").strip()


def _provider_heuristic(litellm_prov: str, cat_providers: List[str]) -> Optional[str]:
    """Tente de resoudre un provider litellm vers un provider catalogue par
    heuristique de nom (egalite insensible aux separateurs)."""
    np = _norm_name(litellm_prov)
    for cp in cat_providers:
        if _norm_name(cp) == np:
            return cp
    for cp in cat_providers:
        ncp = _norm_name(cp)
        if np in ncp or ncp in np:
            return cp
    return None


def _match_model(litellm_model: str, cat_models: List[Dict[str, Any]]) -> Optional[str]:
    """Retourne le model_ref catalogue matchant un modele litellm, ou None.

    `cat_models` = liste de {'model_ref', 'pmn'} pour le provider catalogue
    donne. Strategie : egalite exacte (sans prefixe provider litellm), puis
    suffixe, puis insensible separateurs. Renvoie None si 0 ou >1 candidat.
    """
    lm = litellm_model.split("/")[-1]
    nlm = _norm_name(lm)

    exact, suffix, fuzzy = [], [], []
    for m in cat_models:
        ref = m["model_ref"]
        ref_suffix = ref.split("/")[-1]
        if ref == lm or ref == litellm_model:
            exact.append(ref)
        elif ref_suffix == lm:
            suffix.append(ref)
        elif _norm_name(ref) == nlm or _norm_name(ref_suffix) == nlm:
            fuzzy.append(ref)

    for pool in (exact, suffix, fuzzy):
        if len(pool) == 1:
            return pool[0]
    return None


def discover_litellm_aliases(cat, pricing: Dict[str, Any],
                             dry_run: bool = False) -> Dict[str, int]:
    """Decouvre et insere les alias litellm -> catalogue.

    `pricing` = dict {"provider/model": {...}} issu de fetch_litellm_pricing.
    Retourne un resume {'provider_aliases', 'model_aliases', 'skipped'}.
    """
    cat_prov_rows = cat.conn.execute("SELECT ref FROM catalogue_providers").fetchall()
    cat_providers = [r["ref"] for r in cat_prov_rows]

    pm_rows = cat.conn.execute("""
        SELECT p.ref AS provider_ref, m.ref AS model_ref,
               pm.provider_model_name AS pmn
        FROM provider_models pm
        JOIN catalogue_providers p ON p.id = pm.provider_id
        JOIN catalogue_models m ON m.id = pm.model_id
    """).fetchall()
    models_by_prov: Dict[str, List[Dict[str, Any]]] = {}
    for r in pm_rows:
        models_by_prov.setdefault(r["provider_ref"], []).append(dict(r))

    declared_prov_alias = cat.alias_map(LITELLM_TARGET, "provider")

    stats = {"provider_aliases": 0, "model_aliases": 0, "skipped": 0}
    inserted_prov, inserted_model = set(), set()

    litellm_providers = sorted({k.split("/", 1)[0] for k in pricing if "/" in k})
    for lp in litellm_providers:
        canon = declared_prov_alias.get(lp) or _provider_heuristic(lp, cat_providers)
        if canon is None:
            stats["skipped"] += 1
            continue
        if lp != canon and lp not in inserted_prov:
            inserted_prov.add(lp)
            if not dry_run:
                cat.add_alias(LITELLM_SOURCE, LITELLM_TARGET, "provider", lp, canon)
            stats["provider_aliases"] += 1

    for key, _ in pricing.items():
        if "/" not in key:
            continue
        lp, lm = key.split("/", 1)
        canon_prov = declared_prov_alias.get(lp) or _provider_heuristic(lp, cat_providers)
        if canon_prov is None or canon_prov not in models_by_prov:
            continue
        matched = _match_model(lm, models_by_prov[canon_prov])
        if matched is None:
            continue
        if key != matched and key not in inserted_model:
            inserted_model.add(key)
            if not dry_run:
                cat.add_alias(LITELLM_SOURCE, LITELLM_TARGET, "model", key, matched)
            stats["model_aliases"] += 1

    return stats


# ── Scraper officiel NVIDIA (build.nvidia.com) ──────────────────────────
NVIDIA_CATALOG_URL = "https://build.nvidia.com/api/catalog/optimized_models"


def discover_nvidia_official_aliases(cat, dry_run: bool = False,
                                     timeout: int = 30) -> Dict[str, Any]:
    """Scrape la liste publique NVIDIA et cree des aliases model (target=litellm)
    vers les model_ref catalogue qui matchent par nom.

    NVIDIA expose ses modeles sous des noms type 'meta/llama-3.1-8b-instruct'
    qui different de nos refs catalogue. On matche par suffixe de nom.
    """
    import json
    import urllib.request

    stats: Dict[str, Any] = {"model_aliases": 0, "error": None}
    try:
        req = urllib.request.Request(
            NVIDIA_CATALOG_URL, headers={"User-Agent": "modelweaver-sync/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        stats["error"] = str(e)[:120]
        return stats

    nv_models = []
    if isinstance(data, dict):
        for k in ("models", "items", "data"):
            if isinstance(data.get(k), list):
                nv_models = data[k]
                break
    elif isinstance(data, list):
        nv_models = data

    cat_nvidia = cat.conn.execute("""
        SELECT m.ref AS model_ref, pm.provider_model_name AS pmn
        FROM provider_models pm
        JOIN catalogue_models m ON m.id = pm.model_id
        JOIN catalogue_providers p ON p.id = pm.provider_id
        WHERE p.ref = 'nvidia'
    """).fetchall()
    cat_names = {r["pmn"].split("/")[-1]: r["model_ref"] for r in cat_nvidia}

    inserted = set()
    for item in nv_models:
        name = (item.get("name") or item.get("modelName")
                or item.get("id") or "").lower()
        if not name:
            continue
        suffix = name.split("/")[-1]
        if suffix in cat_names and name != cat_names[suffix]:
            if name not in inserted:
                inserted.add(name)
                if not dry_run:
                    cat.add_alias("nvidia-official", "litellm", "model",
                                  name, cat_names[suffix])
                stats["model_aliases"] += 1
    return stats
