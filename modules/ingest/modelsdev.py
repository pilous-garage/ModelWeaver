"""ingest.modelsdev — téléverseur models.dev → buffer → local.

Conforme à la spec (docs/catalogue_llm_spec.md, 2026-08-18) : l'importeur ne
connaît JAMAIS le domaine local — il dépose des ops dans le buffer
(buffer/write.import_ops, token write_buffer), et le writer local les
consomme (local/write.import_local).

Règle d'or : TOUTE info de models.dev est soit transformée en op (donc en
données du catalogue), soit LOGGÉE (log_error_modeldev) — rien n'est jeté
silencieusement. Les clés inconnues / valeurs inutilisables / cas non
résolus → lignes ERROR ou WARN dans <mw>/logs/log_error_modeldev.log.

Mapping models.dev → domaine local (7 data_types) :

  provider                  → provider          (value = doc/npm/env/api)
  modèle officiel (id sans  → model_official    (déclaration + tags familles/
    préfixe provider/)                           capacités/reasoning/modalities)
  par provider × modèle     → model_provider_endpoint (limits, status,
                             region @xx, typekey :xx, routing provider{}),
                             model_official_id = réconciliation
  suffixes ':typekey'       → provider_typekey  (+ 'default' par provider)
  coûts par (mpe × typekey) → model_provider_endpoint_typekey (input/output/
                             cache_read/write, tiers, context_over_200k)
  endpoint par défaut       → endpoint (api_type = SDK du provider) — les
                             routes par MODÈLE (npm/api/shape) restent sur le
                             MPE, endpoints région détaillés : TODO plus tard.
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from modules.sqlite import local as L
from modules.sqlite.local import local as LL
from modules.sqlite.buffer import write as bwrite

FETCH_URL = "https://models.dev/api.json"
SOURCE = "models.dev"
LOG_NAME = "log_error_modeldev.log"
DEFAULT_BATCH = 500

NS = {
    "provider": "catalogue/llm/provider",
    "endpoint": "catalogue/llm/endpoint",
    "model_official": "catalogue/llm/model",
    "model_provider_endpoint": "catalogue/llm/model_endpoint",
    "provider_typekey": "catalogue/llm/typekey",
    "model_provider_endpoint_typekey": "catalogue/llm/model_typekey",
}

# ── couverture exhaustive des champs ────────────────────────
# RÈGLE : CHAQUE champ de models.dev a un traitement annoté (col/name,
# tags:…, value = conservé dans le JSON brut de la data). Toute clé ABSENTE
# de ce mapping est une ERREUR (sortie log_error_modeldev) — le champ est
# quand même conservé dans `value`, mais l'erreur signale le manque de
# traitement. Justifications : mappés à partir du scan réel api.json
# (2026-08-18) — ré-analyser le snapshot dans ~6 mois pour vérifier.
FIELD_MAP: Dict[str, Dict[str, str]] = {
    "provider": {  # clés top d'un provider
        "id": "col:name", "name": "col:description (provider)",
        "doc": "value (doc)",
        "npm": "tags:provider.npm", "env": "tags:provider.env",
        "api": "tags:provider.api_type",
        "models": "structure (children, non un champ)",
    },
    "model": {  # clés top d'une entrée modèle
        "id": "col:name (mpe_name normalisé)",
        "name": "col:description (mpe) + value",
        "description": "col:description (official) + value",
        "family": "tags:model_official.family",
        "attachment": "tags:capability (+value)",
        "reasoning": "tags:capability (+value)",
        "reasoning_options": "tags:model_official.reasoning_options (liste JSON)",  # noqa: E501
        "tool_call": "tags:capability (+value)",
        "structured_output": "tags:capability (+value)",
        "temperature": "tags:capability (+value)",
        "knowledge": "tags:model_official.knowledge_cutoff",
        "release_date": "tags:model_official.release_date",
        "last_updated": "value (conservé brut)",
        "open_weights": "tags:model_official.is_open_weights",
        "status": "tags:mpe.status",
        "interleaved": "tags:model_official.interleaved (JSON bool|{field})",
        "input_audio": "value (conservé brut)",
        "output_audio": "value (conservé brut)",
        "modalities": "structure (input/output)",
        "limit": "structure (context/input/output)",
        "cost": "structure (coûts → MPET)",
        "experimental": "structure (modes)",
        "provider": "structure (npm/api/shape/body/headers)",
        "modes": "value (conservé brut)",
        "npm": "value (conservé brut)",
        "service_tier": "value (conservé brut)",
    },
    "limit": {"context": "tags:mpe.context_window_tokens",
              "input": "tags:mpe.input_limit_tokens",
              "output": "tags:mpe.max_output_tokens"},
    "cost": {"input": "tags:mpet.cost_in_per_1m",
             "output": "tags:mpet.cost_out_per_1m",
             "cache_read": "tags:mpet.cache_read_per_1m",
             "cache_write": "tags:mpet.cache_write_per_1m",
             "reasoning": "tags:mpet.cost_think_per_1m",
             "input_audio": "tags:mpet.cost_input_audio_per_1m",
             "output_audio": "tags:mpet.cost_output_audio_per_1m",
             "tiers": "tags:mpet.cost_tiers (liste {input/output/…, tier})",
             "context_over_200k": "value (conservé brut — tiers équivalents "
                                  "disponibles en tag)"},
    "modalities": {"input": "tags:model_official.modalities_input",
                   "output": "tags:model_official.modalities_output"},
    "reasoning_option": {"type": "tags:reasoning_options (élément JSON)",
                         "values": "tags:reasoning_options (élément JSON)",
                         "min": "tags:reasoning_options (élément JSON)",
                         "max": "tags:reasoning_options (élément JSON)"},
    "interleaved": {"field": "tags:model_official.interleaved (JSON)"},
    "experimental": {"modes": "tags:mpe.experimental_modes"},
    "experimental_mode": {"cost": "tags:mpe.experimental_modes[].cost",
                          "provider": "tags:mpe.experimental_modes[].provider"},
    "provider_sub": {"npm": "tags:mpe.route_npm",
                     "api": "tags:mpe.route_api",
                     "shape": "tags:mpe.route_shape",
                     "body": "tags:mpe.experimental_modes[].body (JSON)",
                     "headers": "tags:mpe.experimental_modes[].headers (JSON)"},
}

# structures imbriquées traitées par les mappers dédiés
_MODEL_TREE = ("limit", "cost", "modalities", "reasoning_options",
               "interleaved", "experimental", "provider")


class IngestLog:
    """Journal d'ingestion : lignes ISO | Niveau | message, écrites dans
    <mw>/logs/log_error_modeldev.log (levée de l'exception : WARN si absent)."""

    def __init__(self, path: Optional[Path] = None):
        from modules.sqlite.paths import mw_home
        self.path = Path(path) if path else mw_home() / "logs" / LOG_NAME
        self.lines: List[str] = []
        self.errors = 0
        self.warnings = 0

    def _emit(self, level: str, msg: str) -> None:
        line = f"{date.today().isoformat()} | {level} | {msg}"
        self.lines.append(line)
        if level == "ERROR":
            self.errors += 1
        elif level == "WARN":
            self.warnings += 1

    def error(self, msg: str) -> None:
        self._emit("ERROR", msg)

    def warn(self, msg: str) -> None:
        self._emit("WARN", msg)

    def done(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")

    def summary(self) -> Dict[str, int]:
        return {"errors": self.errors, "warnings": self.warnings}


# ── fetch ──────────────────────────────────────────────────

def fetch(url: str = FETCH_URL, cache_path: Optional[Path] = None,
          timeout: int = 60) -> Dict[str, Any]:
    """Télécharge api.json (le snapshot complet : providers × modèles)."""
    if cache_path and cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    return data


# ── décodage des ids ────────────────────────────────────────

def split_id(raw: str) -> Tuple[str, str, str]:
    """Découpe un id de modèle models.dev en (base, typekey, region).

    - typekey : tout ce qui suit le DERNIER ':' quand il est APRÈS le
      dernier '/' (ou s'il n'y a pas de '/') — ex. 'openai/gpt-oss-20b:free'
      → ('openai/gpt-oss-20b', 'free', ''). 'hf:org/model' n'est PAS un
      typekey (les ':' avant le '/' des ids HuggingFace).
    - region : tout ce qui suit le dernier '@' SI la partie après '@' ne
      contient pas de '/' (codes courts : eu, us, jp, global…). Les ids
      '@cf/org/model' de workers (préfixe, pas région) ne sont PAS coupés."""
    region = ""
    base = raw
    if "@" in raw:
        after = raw.rsplit("@", 1)[1]
        if "/" not in after:
            base, region = raw.rsplit("@", 1)
    typekey = ""
    if ":" in base:
        last_slash = base.rfind("/")
        last_colon = base.rfind(":")
        if last_colon > last_slash:
            typekey = base[last_colon + 1:]
            base = base[:last_colon]
    return base, typekey, region


def official_base(raw_id: str, typekey: str = "") -> str:
    """Clé de réconciliation officielle d'un id : la partie SANS le
    préfixe provider (tout ce qui précède le premier '/'), débarrassée
    du suffixe de type de clé porté par l'id ('glm-5.2-free' →
    'glm-5.2', 'gpt-oss-20b:free' → 'gpt-oss-20b')."""
    b = raw_id.split("/", 1)[1] if "/" in raw_id else raw_id
    for suffix in (f":{typekey}", "-free"):
        if b.endswith(suffix):
            b = b[: -len(suffix)]
    return b


# ── parse ───────────────────────────────────────────────────

def parse(data: Dict[str, Any], log: IngestLog) -> Dict[str, Any]:
    """Transforme la sortie brute de models.dev en records exploitables.
    Toute information non transformable → log (ERROR/WARN)."""
    providers: Dict[str, Dict[str, Any]] = {}
    officials: Dict[str, Dict[str, Any]] = {}
    mpes: List[Dict[str, Any]] = []
    typekeys: Dict[str, Dict[str, Dict[str, Any]]] = {}
    costs: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    for pid, pdata in data.items():
        if not isinstance(pdata, dict):
            log.error(f"provider {pid!r}: valeur non-dict — ignorée")
            continue
        unknown = set(pdata) - set(FIELD_MAP["provider"])
        for k in sorted(unknown):
            log.error(f"provider {pid}: champ non traité — provider.{k} "
                      f"(mappage inconnu ; conservé dans value)")
        providers[pid] = {
            "id": pdata.get("id", pid), "name": pdata.get("name", pid),
            "doc": pdata.get("doc", ""), "npm": pdata.get("npm", ""),
            "env": pdata.get("env", []), "api": pdata.get("api", ""),
            "raw": pdata,
        }
        typekeys.setdefault(pid, {"default": {"free_tier": False}})
        models = pdata.get("models") or {}
        if not isinstance(models, dict):
            log.error(f"provider {pid}: models non-dict — ignorés")
            continue
        for mid, m in models.items():
            if not isinstance(m, dict):
                log.error(f"{pid}/{mid}: entrée modèle non-dict — ignorée")
                continue
            _check_model_keys(pid, mid, m, log)
            base, typekey, region = split_id(m.get("id", mid))
            if not base:
                log.error(f"{pid}/{mid}: id sans nom de base — ignoré")
                continue
            if typekey:
                tk = typekeys.setdefault(pid, {}).setdefault(
                    typekey, {"free_tier": typekey == "free",
                              "source_suffix": typekey})
                tk.setdefault("source_suffix", typekey)
                if typekey == "free":
                    tk["free_tier"] = True
            # déclaration official : id SANS '/' (déclarée nativement)
            if "/" not in (m.get("id", mid)):
                officials.setdefault(base, m)
            mpes.append({
                "pid": pid, "raw_id": m.get("id", mid), "base": base,
                "typekey": typekey, "region": region, "entry": m,
            })
            cost = m.get("cost") or {}
            if cost:
                _check_cost(pid, mid, cost, log)
                costs[(pid, base, typekey)] = cost
            else:
                log.warn(f"{pid}/{mid}: pas de coût — pas de MPET")

    return {"providers": providers, "officials": officials, "mpes": mpes,
            "typekeys": typekeys, "costs": costs}


def _audit(level: str, ctx: str, keys, map_name: str, log: IngestLog,
           tree: tuple = ()) -> None:
    """RÈGLE champ → traitement : toute clé d'un champ observé ABSENTE du
    FIELD_MAP sort en ERREUR (le champ reste conservé dans value, mais le
    manque de traitement est signalé)."""
    for k in sorted(set(keys) - set(tree)):
        if k not in FIELD_MAP[map_name]:
            log.error(f"{ctx}: champ non traité — {level}.{k} (mappage "
                      f"inconnu ; conservé dans value)")


def _check_model_keys(pid: str, mid: str, m: Dict[str, Any],
                      log: IngestLog) -> None:
    _audit("model", f"{pid}/{mid}", m, "model", log, tree=_MODEL_TREE)
    lim = m.get("limit")
    if lim is not None:
        if not isinstance(lim, dict):
            log.error(f"{pid}/{mid}: limit non-dict — limite ignorée")
        else:
            _audit("limit", f"{pid}/{mid}", lim, "limit", log)
            for k in ("context", "input", "output"):
                v = lim.get(k)
                if v is not None and not isinstance(v, (int, float)):
                    log.error(f"{pid}/{mid}: limit.{k} non numérique ({v!r})")
    mod = m.get("modalities")
    if mod is not None:
        if not isinstance(mod, dict):
            log.error(f"{pid}/{mid}: modalities non-dict")
        else:
            _audit("modalities", f"{pid}/{mid}", mod, "modalities", log)
    for i, ro in enumerate(m.get("reasoning_options") or []):
        if not isinstance(ro, dict):
            log.warn(f"{pid}/{mid}: reasoning_options[{i}] non-dict")
        else:
            _audit("reasoning_options", f"{pid}/{mid}", ro,
                   "reasoning_option", log)
    exp = m.get("experimental")
    if exp is not None:
        if not isinstance(exp, dict):
            log.error(f"{pid}/{mid}: experimental non-dict")
        else:
            _audit("experimental", f"{pid}/{mid}", exp, "experimental", log)
            for mode, md in (exp.get("modes") or {}).items():
                if not isinstance(md, dict):
                    log.error(f"{pid}/{mid}: experimental.modes.{mode} "
                              f"non-dict")
                    continue
                _audit(f"experimental.modes.{mode}", f"{pid}/{mid}", md,
                       "experimental_mode", log)
                prov = md.get("provider")
                if prov is not None:
                    if not isinstance(prov, dict):
                        log.error(f"{pid}/{mid}: experimental.modes.{mode}."
                                  f"provider non-dict")
                    else:
                        _audit(f"experimental.modes.{mode}.provider",
                               f"{pid}/{mid}", prov, "provider_sub", log)
    il = m.get("interleaved")
    if il is not None and not isinstance(il, (bool, dict)):
        log.warn(f"{pid}/{mid}: interleaved ni bool ni dict ({il!r})")
    elif isinstance(il, dict):
        _audit("interleaved", f"{pid}/{mid}", il, "interleaved", log)
    prov = m.get("provider")
    if prov is not None:
        if not isinstance(prov, dict):
            log.error(f"{pid}/{mid}: provider (sous-champ modèle) non-dict")
        else:
            _audit("provider", f"{pid}/{mid}", prov, "provider_sub", log)


def _check_cost(pid: str, mid: str, cost: Dict[str, Any],
                log: IngestLog) -> None:
    _audit("cost", f"{pid}/{mid}", cost, "cost", log)
    for k in ("input", "output", "cache_read", "cache_write", "reasoning",
              "input_audio", "output_audio"):
        v = cost.get(k)
        if v is not None and not isinstance(v, (int, float)):
            log.error(f"{pid}/{mid}: cost.{k} non numérique ({v!r})")


# ── build des ops ───────────────────────────────────────────

def mpe_name(pid: str, raw_id: str) -> str:
    """Nom de la data model_provider_endpoint : TOUJOURS préfixé du provider
    (pid/raw_id) — les ids nus ('gpt-4o' chez openai) et les ids préfixés
    ('openai/gpt-4o' chez openrouter) existent SANS collision. ':' et '@'
    → '#' (réservés aux sélecteurs d'accessor / régions non adressables)."""
    return f"{pid}/{raw_id}".replace(":", "#").replace("@", "#")


def mpet_name(pid: str, base: str, typekey: str) -> str:
    return f"{pid}/{base.replace(':', '#')}#{typekey or 'default'}"


def _entry_ts(m: Dict[str, Any]) -> str:
    """Timestamp d'origine de la donnée (last_updated, fallback
    release_date) — la VERSION sera résolue par le writer buffer
    (resolve_version), PAS par l'importeur."""
    return str(m.get("last_updated") or m.get("release_date") or "").strip()


def build_ops(records: Dict[str, Any], version: str,
              local_ro=None, log: Optional[IngestLog] = None) -> List[Dict]:
    """Transforme les records en ops du buffer (payload = quadruple +
    colonnes + tags). `local_ro` (db local en lecture) sert à calculer
    model_official_id (ne sert qu'après seed de la source).

    La version N'EST PAS décidée ici : les entrées modèles portent leur
    timestamp d'origine (payload['timestamp']), le writer buffer résout
    la version (timestamp de la source, sinon date du snapshot). `version`
    reste un override explicite (--version du script)."""
    from modules.sqlite.buffer.write import resolve_version
    ops: List[Dict] = []
    ns = NS
    for pid, pr in records["providers"].items():
        tags: Dict[str, Any] = {}
        if pr["api"]:
            tags["api_type"] = pr["api"]
        if pr["npm"]:
            tags["npm"] = pr["npm"]
        if pr["env"]:
            tags["env"] = list(pr["env"])
        ops.append({"domain": "provider", "op": "add", "payload": {
            "namespace": ns["provider"], "name": pr["id"],
            "version": version, "source": SOURCE,
            "data_value_type": "json",
            "value": {"doc": pr["doc"], "npm": pr["npm"], "env": pr["env"],
                      "api": pr["api"]},
            "description": pr["name"], "tags": tags}})
        ops.append({"domain": "endpoint", "op": "add", "payload": {
            "namespace": ns["endpoint"], "name": f"{pr['id']}/default",
            "version": version, "source": SOURCE,
            "data_value_type": "json",
            "value": {"api": pr["api"]},
            "description": f"endpoint par défaut {pr['name']}",
            "tags": {"api_type": pr["api"] or "unknown",
                     "is_default": True}}})

    for base, m in records["officials"].items():
        ops.append({"domain": "model_official", "op": "add", "payload": {
            "namespace": ns["model_official"], "name": base,
            "timestamp": _entry_ts(m), "version": version, "source": SOURCE,
            "data_value_type": "json", "value": m,
            "description": m.get("description", ""),
            "tags": _official_tags(m)}})

    for mpe in records["mpes"]:
        pid, m = mpe["pid"], mpe["entry"]
        tags = _mpe_tags(mpe, log)
        # réconciliation : déclaration officielle du modèle — le data_id de
        # l'officiel se calcule avec SA version (même règle que le writer :
        # resolve_version sur l'entrée officielle).
        oid = 0
        ob = official_base(mpe["base"], mpe["typekey"])
        if ob in records["officials"]:
            over = resolve_version(
                {"timestamp": _entry_ts(records["officials"][ob]),
                 "value": records["officials"][ob]}, version)
            if local_ro is not None:
                sid = LL.source_id_for(local_ro, SOURCE)
                oid = LL.data_id_of(ns["model_official"], ob, sid, over)
            else:
                oid = -1  # post-résolution possible ; absent = 0
        elif log is not None:
            log.warn(f"{pid}/{mpe['raw_id']}: pas de déclaration officielle "
                     f"de {ob!r} — model_official_id absent")
        payload = {
            "namespace": ns["model_provider_endpoint"],
            "name": mpe_name(pid, mpe["raw_id"]),
            "timestamp": _entry_ts(m), "version": version, "source": SOURCE,
            "data_value_type": "json", "value": m,
            "description": m.get("description", ""), "tags": tags,
        }
        if oid:
            payload["model_official_id"] = oid
        ops.append({"domain": "model_provider_endpoint", "op": "add",
                    "payload": payload})

        tk = mpe["typekey"] or "default"
        cost = records["costs"].get((pid, mpe["base"], mpe["typekey"]))
        if cost is None:
            continue
        ctags = _cost_tags(cost, tk)
        ops.append({"domain": "model_provider_endpoint_typekey",
                    "op": "add", "payload": {
                        "namespace": ns["model_provider_endpoint_typekey"],
                        "name": mpet_name(pid, mpe["base"], tk),
                        "timestamp": _entry_ts(m), "version": version,
                        "source": SOURCE,
                        "data_value_type": "json", "value": cost,
                        "description": m.get("name", ""), "tags": ctags}})

    for pid, tks in records["typekeys"].items():
        for tk, info in tks.items():
            tags = {"source_suffix": tk, "free_tier": info.get("free_tier",
                                                               False)}
            if tk != "default":
                tags["label"] = tk
            ops.append({"domain": "provider_typekey", "op": "add", "payload": {
                "namespace": ns["provider_typekey"],
                "name": f"{pid}:{tk}", "version": version, "source": SOURCE,
                "data_value_type": "json",
                "value": {"typekey": tk, "free_tier": tags["free_tier"]},
                "tags": tags}})
    return ops


def _official_tags(m: Dict[str, Any]) -> Dict[str, Any]:
    tags: Dict[str, Any] = {}
    if m.get("family"):
        tags["family"] = m["family"]
    if m.get("knowledge"):
        tags["knowledge_cutoff"] = m["knowledge"]
    if m.get("release_date"):
        tags["release_date"] = m["release_date"]
    if m.get("open_weights") is not None:
        tags["is_open_weights"] = bool(m["open_weights"])
    mod = m.get("modalities") or {}
    if isinstance(mod, dict):
        if mod.get("input"):
            tags["modalities_input"] = list(mod["input"])
        if mod.get("output"):
            tags["modalities_output"] = list(mod["output"])
    if m.get("reasoning_options"):
        tags["reasoning_options"] = list(m["reasoning_options"])
    il = m.get("interleaved")
    if il is not None:
        tags["interleaved"] = json.dumps(il, ensure_ascii=False)
    caps = [c for c in ("attachment", "tool_call", "structured_output",
                        "temperature") if m.get(c)]
    if m.get("reasoning"):
        caps.append("reasoning")
    if m.get("input_audio"):
        caps.append("input_audio")
    if m.get("output_audio"):
        caps.append("output_audio")
    if caps:
        tags["capability"] = caps
    return tags


def _mpe_tags(mpe: Dict[str, Any], log: Optional[IngestLog]) -> Dict[str, Any]:
    pid, m = mpe["pid"], mpe["entry"]
    tags: Dict[str, Any] = {}
    lim = m.get("limit") or {}
    if isinstance(lim, dict):
        if lim.get("context") is not None:
            tags["context_window_tokens"] = lim["context"]
        if lim.get("input") is not None:
            tags["input_limit_tokens"] = lim["input"]
        if lim.get("output") is not None:
            tags["max_output_tokens"] = lim["output"]
    if m.get("status"):
        tags["status"] = m["status"]
    if mpe["region"]:
        tags["region"] = mpe["region"]
    name = m.get("name", mpe["raw_id"])
    if mpe["typekey"] == "free" or name.rstrip().endswith("-free"):
        tags["free_tier"] = True
    if mpe["typekey"]:
        tags["typekey"] = mpe["typekey"]
    prov = m.get("provider")
    if isinstance(prov, dict):
        if prov.get("npm"):
            tags["route_npm"] = prov["npm"]
        if prov.get("api"):
            tags["route_api"] = prov["api"]
        if prov.get("shape"):
            tags["route_shape"] = prov["shape"]
    exp_modes = _experimental_modes(m, log, pid)
    if exp_modes:
        tags["experimental_modes"] = exp_modes
    return tags


def _experimental_modes(m: Dict[str, Any], log: Optional[IngestLog],
                        pid: str) -> List[Dict[str, Any]]:
    exp = m.get("experimental")
    if not isinstance(exp, dict):
        return []
    out: List[Dict[str, Any]] = []
    for mode, md in (exp.get("modes") or {}).items():
        if not isinstance(md, dict):
            continue
        item: Dict[str, Any] = {"mode": mode}
        if isinstance(md.get("cost"), dict):
            item["cost"] = md["cost"]
        prov = md.get("provider")
        if isinstance(prov, dict):
            for k in ("npm", "api", "shape"):
                if prov.get(k):
                    item[k] = prov[k]
            if isinstance(prov.get("body"), dict):
                item["body"] = prov["body"]
            if isinstance(prov.get("headers"), dict):
                item["headers"] = prov["headers"]
        out.append(item)
    return out


def _cost_tags(cost: Dict[str, Any], tk: str) -> Dict[str, Any]:
    tags: Dict[str, Any] = {"typekey_type": tk}
    for k, tag in (("input", "cost_in_per_1m"), ("output", "cost_out_per_1m"),
                   ("cache_read", "cache_read_per_1m"),
                   ("cache_write", "cache_write_per_1m"),
                   ("reasoning", "cost_think_per_1m"),
                   ("input_audio", "cost_input_audio_per_1m"),
                   ("output_audio", "cost_output_audio_per_1m")):
        v = cost.get(k)
        if isinstance(v, (int, float)):
            tags[tag] = v
    if cost.get("tiers"):
        tags["cost_tiers"] = cost["tiers"]
    if tk == "free" or cost.get("input") == 0:
        tags["free_tier"] = True
    return tags


# ── écriture buffer + consommation locale ───────────────────

def push(buffer_w, ops: List[Dict[str, Any]], batch: int = DEFAULT_BATCH,
         external_tag: str = SOURCE, source: str = SOURCE, version: str = "",
         token: str = "") -> Dict[str, Any]:
    """Dépose les ops par mini-batchs (pending) — UNE écriture par batch.
    La source est passée au WRITER BUFFER (qui résout aussi les versions
    manquantes via resolve_version)."""
    total = 0
    t = token or "write_buffer"
    for i in range(0, len(ops), batch):
        res = bwrite.import_ops(buffer_w, ops[i:i + batch],
                                external_tag=external_tag, source=source,
                                version=version, token=t)
        total += res["pushed"]
    return {"ok": True, "pushed": total}


def sync(local_w, buffer_w, limit: int = 500, purge: bool = True,
         token: str = "") -> Dict[str, Any]:
    """import_local : le writer LOCAL consomme les ops pending du buffer
    (mini-batchs bouclés jusqu'à épuisement)."""
    from modules.sqlite.local import write as lwrite
    total = {"applied": 0, "errors": 0, "purged": 0}
    while True:
        r = lwrite.import_local(buffer_w, local_w, limit=limit,
                                purge=purge, token=token)
        total["applied"] += r["applied"]
        total["errors"] += r["errors"]
        total["purged"] += r.get("purged", 0)
        if r.get("pending_left", 0) == 0:
            break
    return total


def run(data: Dict[str, Any], buffer_w, local_w, version: str = "",
        log_path: Optional[Path] = None, batch: int = DEFAULT_BATCH,
        limit: int = 2000, consume: bool = True) -> Dict[str, Any]:
    """Pipeline complet : parse → build_ops → push buffer → consume local.
    version : override explicite (--version) ; vide → le writer buffer
    résout chaque version (timestamp de la source / date du snapshot)."""
    log = IngestLog(log_path)
    records = parse(data, log)
    ops = build_ops(records, version, local_ro=local_w, log=log)
    pushed = push(buffer_w, ops, batch=batch, source=SOURCE,
                  version=version)
    consumed = {"applied": 0, "errors": 0, "purged": 0}
    if consume and pushed["pushed"]:
        consumed = sync(local_w, buffer_w, limit=limit,
                        token="write_catalogue")
    log.done()
    return {"records": {k: len(v) if isinstance(v, (dict, list)) else v
                        for k, v in records.items()},
            "ops": pushed, "consumed": consumed, "log": log.summary()}