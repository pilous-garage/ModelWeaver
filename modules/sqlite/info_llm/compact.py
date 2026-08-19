"""compact — le COMPACTEUR : local_catalogue v4 (ro) → info_llm (write).

Le compacteur est l'UNIQUE writer d'info_llm (token write_info_llm). Il
régénère les tables stables du bridge en passes idempotentes (upsert par
clés UNIQUE, ids conservés entre régénérations) :

  provider                  → catalogue_providers
  endpoint                  → provider_endpoints
  model_official            → catalogue_models + model_capabilities
  model_provider_endpoint   → provider_models + provider_models_mapping
                             + provider_endpoint_api_key_type
                             + endpoint_apikeytype_model_adress
  model_provider_endpoint_typekey → cost_final

Règles :
  - par (type, name) local : source la plus prioritaire
    (user > entreprise > official > models.dev > distant/friend/git),
    puis version la plus récente ;
  - chaque ligne porte local_data_id (data_id v4) pour tracer la provenance ;
  - model_capabilities/alias_model/budgets* (annotations) jamais touchés ;
  - réconciliation MPE → modèle officiel via `model_official_id` local,
    fallback official_base() sur le nom.

Usage :
    from modules.sqlite.info_llm import compact
    res = compact.compact(L.db_ro(), I.get_writer(I.WRITE_INFO_LLM_TOKEN))
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from modules.sqlite.base import Db
from modules.sqlite.info_llm import WRITE_INFO_LLM_TOKEN
from modules.sqlite.info_llm.write import regenerate
from modules.sqlite.local import local as LL

SOURCE_PRIORITY = ["user", "enterprise", "official", "models.dev",
                    "distant", "friend", "git_depot"]

# URL d'endpoint par défaut par provider_ref — les données models.dev ne
# portent pas d'URL ; le routeur (v3) appliquait ces templates ici mêmes.
# info_llm devient la source de vérité : le bridge migré lira endpoint_url
# sans dupliquer ce registre.
DEFAULT_ENDPOINT_URLS = {
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "deepinfra": "https://api.deepinfra.com/v1/openai",
    "ollama": "http://localhost:11434/v1",
    "ollama-cloud": "https://ollama.com/v1",
    "github-models": "https://models.inference.ai.azure.com",
    "openai": "https://api.openai.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "anthropic": "https://api.anthropic.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "fireworks": "https://api.fireworks.ai/inference/v1",
    "groq_cloud": "https://api.groq.com/openai/v1",
}

# URL par api_type (fallback générique quand le provider n'est pas connu)
API_TYPE_URLS = {
    "openai_compatible": "https://api.openai.com/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "ollama": "http://localhost:11434/v1",
}

# api_type (SDK) par défaut par provider_ref, quand la donnée ne le donne pas
# (ex. openai/anthropic ont api="" chez models.dev). Sert à peupler `sdk`
# sur les adresses pour que le bridge sache quel client utiliser.
API_TYPE_BY_PROVIDER = {
    "openai": "openai", "anthropic": "anthropic", "google": "google",
    "gemini": "google", "groq": "openai_compatible", "ollama": "ollama",
    "ollama-cloud": "ollama", "nvidia": "openai_compatible",
    "together": "openai_compatible", "deepinfra": "openai_compatible",
    "openrouter": "openai_compatible", "mistral": "openai_compatible",
    "fireworks": "openai_compatible",
}

# (type_local, table_info_llm) — ordre des passes
_TYPES = ("provider", "endpoint", "model_official",
          "model_provider_endpoint", "model_provider_endpoint_typekey")


def compact(local_ro: Db, info_w: Db,
            source_priority: Optional[List[str]] = None,
            batch: int = 1000, token: str = "", log=None,
            source_ref: str = "") -> Dict[str, Any]:
    """Régénère info_llm depuis local_catalogue. Retourne les compteurs.

    Sélection des lignes : par (type, name), la source la plus prioritaire
    (user > entreprise > official > models.dev > distant/friend/git) ; à
    source égale, la version la plus récente. `source_ref` (obsolète) force
    une seule source (équivalent à une priorité à un élément).
    """
    token = token or WRITE_INFO_LLM_TOKEN
    if source_ref:
        priority = [source_ref]
    else:
        priority = list(source_priority or SOURCE_PRIORITY)
    rank = _source_rank(local_ro, priority)
    best = {t: _best_rows(local_ro, t, rank) for t in _TYPES}
    tags = {t: _tags_index(local_ro, t)
            for t in ("model_official", "model_provider_endpoint",
                      "model_provider_endpoint_typekey")}
    log = log or _NullLog()
    counts: Dict[str, int] = {}

    # ── passe 1 : providers, endpoints, modèles (réfs stables) ──────────
    reg = regenerate(info_w, {
        "catalogue_providers": [_provider_row(r) for r
                                in best["provider"].values()],
    }, batch=batch, token=token)
    counts.update(reg["tables"])
    pids = {r["ref"]: r["provider_id"] for r in
            info_w.table("catalogue_providers").select()}

    ep_rows = []
    prov_api: Dict[str, str] = {r["name"]: (_value(r).get("api") or "")
                                for r in best["provider"].values()}
    for r in best["endpoint"].values():
        pid = r["name"].split("/", 1)[0]
        v = _value(r)
        api_type = v.get("api") or prov_api.get(pid, "")
        url = v.get("url")
        if not url:
            if isinstance(api_type, str) and api_type.startswith("http"):
                url = api_type
            else:
                url = DEFAULT_ENDPOINT_URLS.get(pid) or _api_type_url(api_type)
        clean_api = _clean_api_type(api_type) or API_TYPE_BY_PROVIDER.get(pid, "")
        ep_rows.append({
            "ref": r["name"], "provider_id": pids.get(pid, 0),
            "endpoint_url": url, "api_type": clean_api,
            "local_data_id": r["data_id"]})
    reg = regenerate(info_w, {"provider_endpoints": ep_rows},
                     batch=batch, token=token)
    counts.update(reg["tables"])
    eids = {r["ref"]: r["endpoint_id"] for r in
            info_w.table("provider_endpoints").select()}

    reg = regenerate(info_w, {
        "catalogue_models": [_model_row(r) for r
                             in best["model_official"].values()],
    }, batch=batch, token=token)
    counts.update(reg["tables"])
    mids = {r["ref"]: r["model_id"] for r in
            info_w.table("catalogue_models").select()}

    # ── passe 2 : capacités + provider_models (ids résolus) ─────────────
    # Source weight map: rank 0=user/official/enterprise→1.0, rank 3=models.dev→0.9,
    # ranks 4-6 (distant/friend/git_depot)→0.8/0.7/0.7
    source_weight_map = {0: 1.0, 1: 1.0, 2: 1.0, 3: 0.9, 4: 0.8, 5: 0.7, 6: 0.7}
    caps = []
    for r in best["model_official"].values():
        mid = mids.get(r["name"])
        if mid is None:
            continue
        # Rang source depuis _source_rank (0=plus prioritaire)
        srank = _source_rank(local_ro, priority or SOURCE_PRIORITY).get(
            r["source_id"], len(priority or SOURCE_PRIORITY))
        sw = source_weight_map.get(srank, 0.6)
        # Référence source (pour source_ref dans les lignes capabilities)
        sref = _source_ref_from_id(local_ro, r["source_id"])
        # Âge depuis version
        age_days = _age_days(r.get("version", ""))
        caps.extend(_capability_row(r, mid, tags["model_official"].get(
            r["data_id"], {}), sw, sref, age_days))
    pms = []
    for r in best["model_provider_endpoint"].values():
        pid = r["name"].split("/", 1)[0]
        if pid not in pids:
            continue
        key_tag, base, pm_name = _dissect(r["name"], tags[
            "model_provider_endpoint"].get(r["data_id"], {}))
        ref = _official_ref(local_ro, r) or _strip_free(base)
        mid = mids.get(ref)
        if mid is None:
            log.warn(f"compact: mpe {r['name']}: pas de modèle officiel "
                     f"({ref!r})")
            continue
        v = _value(r)
        tg = tags["model_provider_endpoint"].get(r["data_id"], {})
        pms.append({
            "provider_id": pids[pid], "model_id": mid,
            "name": r["name"], "provider_model_name": pm_name,
            "key_tag": key_tag,
            "free_tier": _tag_bool(tg, "free_tier"),
            "context_window": _limit_ctx(v),
            "status": _tag_one(tg, "status", "active"),
            "local_data_id": r["data_id"]})
    reg = regenerate(info_w, {"model_capability": caps,
                              "provider_models": pms},
                     batch=batch, token=token)
    counts.update(reg["tables"])
    pm_rows = info_w.table("provider_models").select()

    # ── passe 3 : matrice clés, mapping, adresses, tarifs ───────────────
    mid_to_ref = {v: k for k, v in mids.items()}
    # matrice provider_endpoint_api_key_type (depuis provider_typekey v4)
    pkt = _provider_typekeys(local_ro)
    ep_rows = info_w.table("provider_endpoints").select(
        cols=["endpoint_id", "ref", "provider_id", "endpoint_url",
              "api_type"])
    ep_by_prov: Dict[int, List[Dict]] = {}
    for r in ep_rows:
        ep_by_prov.setdefault(r["provider_id"], []).append(r)
    matrix = []
    for pname, pid in pids.items():
        eps = ep_by_prov.get(pid, [])
        types = pkt.get(pname, [])
        if not types:
            types = ["unknown"]          # fallback coherent (pas de typekey)
        for ep in eps:
            for tk in types:
                matrix.append({"provider_id": pid, "provider_ref": pname,
                               "endpoint_id": ep["endpoint_id"],
                               "endpoint_ref": ep["ref"],
                               "api_key_type": tk})
    reg = regenerate(info_w,
                     {"provider_endpoint_api_key_type": matrix},
                     batch=batch, token=token)
    counts.update(reg["tables"])
    # index matrice : provider_id -> [(endpoint_id, endpoint_ref,
    #                                  api_key_type, url, sdk)]
    sdks = {r["endpoint_id"]: r["api_type"] for r in ep_rows}
    eurls = {r["endpoint_id"]: r["endpoint_url"] for r in ep_rows}
    matrix_idx: Dict[int, List[Dict]] = {}
    for r in info_w.table("provider_endpoint_api_key_type").select():
        matrix_idx.setdefault(r["provider_id"], []).append({
            "endpoint_id": r["endpoint_id"], "endpoint_ref": r["endpoint_ref"],
            "api_key_type": r["api_key_type"]})

    mapping = []
    addresses = []
    for r in pm_rows:
        key_tag, base, pm_name = _dissect(r["name"], {})
        ref = mid_to_ref.get(r["model_id"], base)
        provider_ref = r["name"].split("/", 1)[0]
        mapping.append({"model_key": ref,
                        "provider_model_id": r["provider_model_id"]})
        for m in matrix_idx.get(r["provider_id"], []):
            ep_id = m["endpoint_id"]
            addresses.append({
                "endpoint_id": ep_id,
                "endpoint_ref": m["endpoint_ref"],
                "endpoint_url": eurls.get(ep_id, ""),
                "api_key_type": m["api_key_type"],
                "provider_id": r["provider_id"],
                "provider_ref": provider_ref,
                "model_endpoint_id": r["provider_model_id"],
                "model_id": r["model_id"], "model_key": ref,
                "provider_model_name": r["provider_model_name"],
                "sdk": sdks.get(ep_id, ""),
                "available": 1, "deprecated": 0,
                "local_data_id": r["local_data_id"]})

    costs = []
    pm_idx: Dict[Tuple[int, int, str], Dict[str, Any]] = {}
    for r in pm_rows:
        pm_idx[(r["provider_id"], r["model_id"], r["key_tag"])] = r
    for r in best["model_provider_endpoint_typekey"].values():
        v = _value(r)
        if not v:
            log.warn(f"compact: typekey {r['name']}: value vide — ignoré")
            continue
        pid = r["name"].split("/", 1)[0]
        key_tag = r["name"].rsplit("#", 1)[1] if "#" in r["name"] else ""
        if key_tag == "default":
            key_tag = ""
        base = r["name"].split("/", 1)[1].rsplit("#", 1)[0]
        ref = _official_ref(local_ro, r) or _strip_free(base)
        mid = mids.get(ref)
        if mid is None:
            continue
        pm = pm_idx.get((pids.get(pid, 0), mid, key_tag))
        tg = tags["model_provider_endpoint_typekey"].get(r["data_id"], {})
        costs.append({
            "model_id": mid,
            "provider_model_id": pm["provider_model_id"] if pm else None,
            "key_tag": key_tag,
            "input_per_1m": v.get("input"),
            "output_per_1m": v.get("output"),
            "free_tier": _tag_bool(tg, "free_tier"),
            "local_data_id": r["data_id"]})
    reg = regenerate(info_w, {
        "provider_models_mapping": mapping,
        "endpoint_apikeytype_model_adress": addresses,
        "cost_final": costs,
    }, batch=batch, token=token)
    counts.update(reg["tables"])

    # ── invariants de complétude (erreur si violé, sinon warning) ───────
    inv = _check_invariants(info_w, log)
    counts["invariants"] = inv
    counts["local_rows_scanned"] = sum(len(best[t]) for t in _TYPES)
    return {"ok": True, "counts": counts}


# ── lecture local v4 ─────────────────────────────────────────

def _source_rank(db: Db, priority: List[str]) -> Dict[int, int]:
    """Mappe source_id → rang de priorité (0 = plus prioritaire).

    Les sources non listées reçoivent un rang très bas (utilisées seulement
    s'il n'y a aucune source prioritaire pour ce name)."""
    rows = db.sql("SELECT sources_id, ref FROM global_local_source")
    ref2id = {r["ref"]: r["sources_id"] for r in rows}
    rank: Dict[int, int] = {}
    for i, ref in enumerate(priority):
        if ref in ref2id:
            rank[ref2id[ref]] = i
    return rank


def _best_rows(db: Db, type_: str, rank: Dict[int, int]) -> Dict[str, Dict]:
    """Par (type, name) : source la plus prioritaire (rang minimal) ; à
    source égale, version la plus récente."""
    cols = ["data_id", "namespace", "name", "source_id", "version",
            "value", "description"]
    if type_ == "model_provider_endpoint":
        cols.append("model_official_id")
    rows = db.table(f"{type_}_data").select(cols=cols, order_by="name")
    last = len(rank)  # rang par défaut (le plus bas) pour sources inconnues
    out: Dict[str, Dict] = {}
    for r in rows:
        cur = out.get(r["name"])
        rk = rank.get(r["source_id"], last)
        if cur is None:
            out[r["name"]] = r
            continue
        cur_rk = rank.get(cur["source_id"], last)
        if rk < cur_rk:
            out[r["name"]] = r
        elif rk == cur_rk and \
                LL.version_key(r["version"]) > LL.version_key(cur["version"]):
            out[r["name"]] = r
    return out


def _clean_api_type(api_type: Any) -> str:
    """api_type propre pour le routage : si la source donne une URL, on en
    infère le SDK ; sinon on garde la valeur telle quelle."""
    if not isinstance(api_type, str) or not api_type:
        return ""
    if api_type.startswith("http"):
        u = api_type.lower()
        if "anthropic" in u:
            return "anthropic"
        if "google" in u or "generativelanguage" in u:
            return "google"
        if "groq" in u:
            return "groq"
        return "openai_compatible"
    return api_type


def _tags_index(db: Db, type_: str) -> Dict[int, Dict[str, Any]]:
    rows = db.table(f"{type_}_tag").select(cols=["data_id", "tag_type",
                                                 "tag_value"])
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        v = r["tag_value"]
        try:
            v = json.loads(v)
        except (TypeError, ValueError):
            pass
        out.setdefault(r["data_id"], {})[r["tag_type"]] = v
    return out


def _provider_typekeys(local_ro: Db) -> Dict[str, List[str]]:
    """provider_ref → [api_key_type…] depuis provider_typekey (local v4).

    Le typekey vient de value.typekey. Filtre : on ne garde QUE les tokens
    alphabétiques (les vrais types de clé : free, thinking, low, plus…).
    Les suffixes numériques/tailles (120b, 262144, 0731, 0…) sont des
    variantes de MODÈLE confondues avec des typekeys par split_id() dans
    l'ingest — ils ne sont PAS des types de clé et pollueraient la matrice.
    Aucun fallback ici : si vide, le compacteur seedera 'unknown'."""
    out: Dict[str, List[str]] = {}
    rows = local_ro.table("provider_typekey_data").select(
        cols=["name", "value"])
    for r in rows:
        name = r["name"] or ""
        pid = name.split(":", 1)[0] if ":" in name else name
        v = _value(r)
        tk = v.get("typekey") or ""
        if not tk or not tk.isalpha():   # exclude 120b / 262144 / 0731 / 0…
            continue
        out.setdefault(pid, [])
        if tk not in out[pid]:
            out[pid].append(tk)
    return out


def _check_invariants(info_w: Db, log) -> Dict[str, Any]:
    """Vérifie la complétude du graphe. Les invariants DUR (provider→
    endpoint, provider→api_key_type) lèvent ValueError (ils sont garantis
    par construction) ; les autres sont des warnings comptés."""
    hard: Dict[str, int] = {}
    soft: Dict[str, int] = {}
    prov = info_w.table("catalogue_providers").select(cols=["provider_id"])
    pids = [r["provider_id"] for r in prov]
    # DUR : chaque provider → ≥1 endpoint
    ep_count = {p: 0 for p in pids}
    for r in info_w.table("provider_endpoints").select(cols=["provider_id"]):
        if r["provider_id"] in ep_count:
            ep_count[r["provider_id"]] += 1
    no_ep = [p for p, c in ep_count.items() if c == 0]
    hard["providers_sans_endpoint"] = len(no_ep)
    # DUR : chaque provider → ≥1 api_key_type
    pkt_count = {p: 0 for p in pids}
    for r in info_w.table("provider_endpoint_api_key_type").select(
            cols=["provider_id"]):
        if r["provider_id"] in pkt_count:
            pkt_count[r["provider_id"]] += 1
    no_tk = [p for p, c in pkt_count.items() if c == 0]
    hard["providers_sans_typekey"] = len(no_tk)
    # SOFT : chaque provider → ≥1 model
    pm_count = {p: 0 for p in pids}
    for r in info_w.table("provider_models").select(cols=["provider_id"]):
        if r["provider_id"] in pm_count:
            pm_count[r["provider_id"]] += 1
    soft["providers_sans_model"] = sum(1 for c in pm_count.values() if c == 0)
    # SOFT : chaque endpoint → ≥1 adresse
    epa = {r["endpoint_id"] for r in
           info_w.table("endpoint_apikeytype_model_adress").select(
               cols=["endpoint_id"])}
    ep_all = {r["endpoint_id"] for r in
              info_w.table("provider_endpoints").select(cols=["endpoint_id"])}
    soft["endpoints_sans_adresse"] = len(ep_all - epa)
    for k, v in soft.items():
        if v:
            log.warn(f"invariant (warning): {k} = {v}")
    for k, v in hard.items():
        if v:
            raise ValueError(f"invariant DUR violé: {k} = {v}")
    return {"hard": hard, "soft": soft}


def _value(row: Dict) -> Dict[str, Any]:
    try:
        v = json.loads(row["value"] or "{}")
    except (TypeError, ValueError):
        v = {}
    return v if isinstance(v, dict) else {}


def _tag_one(tags: Dict[str, Any], key: str, default: Any = None) -> Any:
    v = tags.get(key, default)
    if isinstance(v, list):
        return v[0] if v else default
    return v


def _tag_bool(tags: Dict[str, Any], key: str) -> int:
    v = tags.get(key)
    return 1 if v else 0


def _dissect(name: str, tags: Dict[str, Any]) -> Tuple[str, str, str]:
    """name 'pid/raw#suffix' → (key_tag, base, provider_model_name).
    key_tag = tag typekey (le #suffix peut être une région, pas un typekey)."""
    rest = name.split("/", 1)[1] if "/" in name else name
    base = rest.split("#", 1)[0]
    key_tag = _tag_one(tags, "typekey", "")
    pm_name = base.split("#", 1)[0]
    return key_tag, base, pm_name


def _official_ref(local_ro: Db, row: Dict) -> Optional[str]:
    """data_id local du MPE → ref du modèle officiel (colonne
    model_official_id écrite à l'ingest, réconciliation versionnée)."""
    oid = row.get("model_official_id") or 0
    if not oid:
        return None
    r = local_ro.table("model_official_data").get(
        {"data_id": oid}, cols=["name"])
    return r["name"] if r else None


def _strip_free(base: str) -> str:
    if base.endswith("-free"):
        return base[: -len("-free")]
    return base


# ── constructeurs de lignes ──────────────────────────────────

def _provider_row(r: Dict) -> Dict[str, Any]:
    v = _value(r)
    return {"ref": r["name"], "name": r["description"] or r["name"],
            "api_type": v.get("api", ""), "doc": v.get("doc", ""),
            "npm": v.get("npm", ""), "env": json.dumps(v.get("env") or []),
            "local_data_id": r["data_id"]}


def _model_row(r: Dict) -> Dict[str, Any]:
    v = _value(r)
    return {"ref": r["name"],
            "description": r["description"] or v.get("name", ""),
            "family": v.get("family", ""), "local_data_id": r["data_id"]}


def _capability_row(r: Dict, mid: int, tags: Dict[str, Any],
                    source_weight: float, source_ref: str,
                    age_days: float) -> List[Dict[str, Any]]:
    """Génère des lignes model_capability pour un modèle donné.

    Retourne une ligne par capacité détectée/déduite. Valeurs manquantes →
    value='unknown', confiance = baseline × source_weight × age_decay.
    """
    v = _value(r)
    limit = v.get("limit") or {}
    cost = v.get("cost") or {}
    modalities = v.get("modalities") or {}
    tin = modalities.get("input") or []
    tools = 1 if (v.get("tool_call") or
                  "tool_call" in (tags.get("capability") or [])) else 0

    # Décay confiance : 0.5^(age_days / 30) — demi-vie = 30 jours
    decay = 0.5 ** (age_days / 30.0) if age_days >= 0 else 1.0
    base_conf = 0.5  # neutre pour données manquantes

    rows = []

    # 1. vision : 'image' dans modalities input
    rows.append({
        "model_id": mid,
        "capability": "vision",
        "value": "true" if "image" in tin else ("false" if tin else "unknown"),
        "confidence": _calc_confidence("vision", bool("image" in tin if tin else False),
                                       source_weight, decay, base_conf),
        "source_ref": source_ref,
        "last_info": _last_info(v),
        "exp_adjust": 0, "exp_n": 0,
        "local_data_id": r["data_id"]
    })

    # 2. function_calling / tools : tool_call présent
    fc = "true" if tools else ("false" if tin or not tools else "unknown")
    rows.append({
        "model_id": mid,
        "capability": "function_calling",
        "value": "true" if tools else "false",
        "confidence": _calc_confidence("function_calling", tools,
                                       source_weight, decay, base_conf),
        "source_ref": source_ref,
        "last_info": _last_info(v),
        "exp_adjust": 0, "exp_n": 0,
        "local_data_id": r["data_id"]
    })

    # 3. embedding : 'embeddings' dans capacité / modalities
    rows.append({
        "model_id": mid,
        "capability": "embedding",
        "value": "true" if ("embeddings" in (tags.get("capability") or []) or
                           "embeddings" in (modalities.get("output") or [])) else
                     ("false" if tags.get("capability") and "embeddings" not in tags["capability"] else "unknown"),
        "confidence": _calc_confidence("embedding",
                                       "embeddings" in (tags.get("capability") or []) or
                                       "embeddings" in (modalities.get("output") or []),
                                       source_weight, decay, base_conf),
        "source_ref": source_ref,
        "last_info": _last_info(v),
        "exp_adjust": 0, "exp_n": 0,
        "local_data_id": r["data_id"]
    })

    # 4. streaming : toujours vrai par défaut
    rows.append({
        "model_id": mid,
        "capability": "streaming",
        "value": "true",
        "confidence": _calc_confidence("streaming", True,
                                       source_weight, decay, base_conf),
        "source_ref": source_ref,
        "last_info": _last_info(v),
        "exp_adjust": 0, "exp_n": 0,
        "local_data_id": r["data_id"]
    })

    # 5. chat : toujours vrai par défaut
    rows.append({
        "model_id": mid,
        "capability": "chat",
        "value": "true",
        "confidence": _calc_confidence("chat", True,
                                       source_weight, decay, base_conf),
        "source_ref": source_ref,
        "last_info": _last_info(v),
        "exp_adjust": 0, "exp_n": 0,
        "local_data_id": r["data_id"]
    })

    # 6. reasoning : 'reasoning' dans capability ou value
    has_reasoning = ("reasoning" in (tags.get("capability") or []) or
                     "reasoning" in (v.get("reasoning") or ""))
    rows.append({
        "model_id": mid,
        "capability": "reasoning",
        "value": "true" if has_reasoning else "false",
        "confidence": _calc_confidence("reasoning", has_reasoning,
                                       source_weight, decay, base_conf),
        "source_ref": source_ref,
        "last_info": _last_info(v),
        "exp_adjust": 0, "exp_n": 0,
        "local_data_id": r["data_id"]
    })

    # 7. json_mode : 'json_mode' dans capability
    rows.append({
        "model_id": mid,
        "capability": "json_mode",
        "value": "true" if ("json_mode" in (tags.get("capability") or []) or
                           "json_mode" in (v.get("json") or "")) else "false",
        "confidence": _calc_confidence("json_mode",
                                       "json_mode" in (tags.get("capability") or []) or
                                       "json_mode" in (v.get("json") or ""),
                                       source_weight, decay, base_conf),
        "source_ref": source_ref,
        "last_info": _last_info(v),
        "exp_adjust": 0, "exp_n": 0,
        "local_data_id": r["data_id"]
    })

    # 8. long_context : contexte > 32k tokens ou capability 'long_context'
    lctx = limit.get("context")
    has_long = (lctx is not None and lctx > 32000) or \
               ("long_context" in (tags.get("capability") or []))
    rows.append({
        "model_id": mid,
        "capability": "long_context",
        "value": "true" if has_long else "false",
        "confidence": _calc_confidence("long_context", has_long,
                                       source_weight, decay, base_conf),
        "source_ref": source_ref,
        "last_info": _last_info(v),
        "exp_adjust": 0, "exp_n": 0,
        "local_data_id": r["data_id"]
    })

    return rows


def _calc_confidence(cap: str, asserted: bool, source_weight: float,
                     decay: float, base_conf: float) -> float:
    """Calcule la confiance finale = source_weight × decay + base_conf × (1-source_weight×decay)
    mais si value='unknown', on retourne base_conf neutre."""
    if not asserted and cap in ("vision", "function_calling", "embedding", "reasoning",
                                "json_mode", "long_context"):
        # Capabilité non assertée → unknown valeur, confiance baseline
        return base_conf
    # Capabilité assertée : source × decay pondéré
    w = source_weight * decay
    if w >= 1.0:
        return 1.0 if asserted else base_conf
    # Mélange source×decay + baseline restante
    return round(w + base_conf * (1.0 - w), 4)


def _last_info(v: Dict[str, Any]) -> str:
    """Extrait last_info depuis le dict value (version/date)."""
    #essaie divers champs de version/date
    for key in ("version", "date", "last_updated", "release"):
        if key in v and v[key]:
            return str(v[key])
    return ""


def _age_days(version: str) -> float:
    """Retourne l'âge en jours depuis une chaîne de version/date.
    Essaye de parser les formats ISO, YYYY-MM, ou renvoie 0 si inconnu."""
    if not version:
        return 0.0
    v = version.strip()
    # Format ISO complet
    try:
        from datetime import datetime, date
        dt = datetime.fromisoformat(v).date()
        return (date.today() - dt).days
    except Exception:
        pass
    # Format YYYY-MM
    try:
        from datetime import datetime
        dt = datetime.strptime(v, "%Y-%m").date()
        return (date.today() - dt).days
    except Exception:
        pass
    # Nombre arbitraire si on peut extraire une année
    import re
    m = re.search(r"(\d{4})", v)
    if m:
        try:
            yr = int(m.group(1))
            from datetime import date
            return (date.today().replace(month=1, day=1) -
                    date(yr, 1, 1)).days
        except Exception:
            pass
    return 0.0


def _source_ref_from_id(db: Db, source_id: int) -> str:
    """Résolve source_id → ref (ex. 'official', 'models.dev') depuis la table
    global_local_source."""
    try:
        row = db.sql(
            "SELECT ref FROM global_local_source WHERE sources_id = ?",
            [source_id])
        return row[0]["ref"] if row else ""
    except Exception:
        return ""


def _api_type_url(api_type: str) -> str:
    """Fallback : template d'URL par api_type (dernier recours)."""
    return API_TYPE_URLS.get(api_type, "")


def _limit_ctx(v: Dict[str, Any]) -> Optional[int]:
    return (v.get("limit") or {}).get("context")


class _NullLog:
    def warn(self, *a, **k):
        pass