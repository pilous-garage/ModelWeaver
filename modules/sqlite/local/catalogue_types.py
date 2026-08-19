"""catalogue_types — déclaration des data_types du catalogue LLM (block 1).

Spec : docs/catalogue_llm_spec.md (2026-08-18). Les 7 types remplacent le
catalogue LLM historique (~25 tables de `catalogue_schema.sql`) :

    model_official                  le modèle RÉEL (déclaration officielle)
    provider                        le fournisseur
    endpoint                        l'endpoint API (avec api_type = SDK)
    provider_endpoint               N:M provider ↔ endpoint (is_default)
    model_provider_endpoint         le modèle CHEZ (provider × endpoint) —
                                    colonne model_official_id = réconciliation
    provider_typekey                les types de clés du provider
    model_provider_endpoint_typekey capacity + coût + quota (UNE data_type)

Le seed est IDEMPOTENT (CREATE IF NOT EXISTS + upserts) : les types +
registres de tags (x_tag_type) peuvent être re-créés sans perte. Aucune
donnée n'est insérée ici — l'init viendra de models.dev via le buffer
(export_local → import_local du writer local).

Sharing : par défaut TOUT est false/'non' (défauts schéma) — le demandeur
de sharing viendra plus tard.
"""

from __future__ import annotations

from typing import Any, Dict

from modules.sqlite.base import Db
from modules.sqlite.local import data_table, write as lwrite

SOURCE_MODELS_DEV = "models.dev"

# type → (description, colonnes extra)
CATALOGUE_TYPES: Dict[str, Dict[str, Any]] = {
    "model_official": {
        "description": "le modèle RÉEL : déclaration officielle (type HF) "
                       "+ tags (date de conception, capacités…) — SANS "
                       "limites/ajustements provider",
        "extra_cols": {},
    },
    "provider": {
        "description": "fournisseur LLM (cloud/local/ollama/builtin, site, "
                       "api_type par défaut, free_tier)",
        "extra_cols": {},
    },
    "endpoint": {
        "description": "endpoint API (url + templates région/resource, "
                       "api_type = le SDK qui drive le routing)",
        "extra_cols": {},
    },
    "provider_endpoint": {
        "description": "quel provider utilise quel endpoint (N:M, is_default, "
                       "priorité)",
        "extra_cols": {},
    },
    "model_provider_endpoint": {
        "description": "le modèle CHEZ (provider × endpoint) : "
                       "provider_model_name, limites provider, status sourcé, "
                       "capacités du lien",
        "extra_cols": {"model_official_id": "INTEGER"},
    },
    "provider_typekey": {
        "description": "types de clés d'un provider (free/plus/premium/'' — "
                       "tag/k blob/user…)",
        "extra_cols": {},
    },
    "model_provider_endpoint_typekey": {
        "description": "capacity + coût + quota par (model_provider_endpoint × "
                       "provider_typekey) — UNE data_type catalogue",
        "extra_cols": {},
    },
}

CATALOGUE_ORDER = ["model_official", "provider", "endpoint", "provider_endpoint",
                   "model_provider_endpoint", "provider_typekey",
                   "model_provider_endpoint_typekey"]

# type → {tag_type: (tag_value_type, description)} — registres x_tag_type que
# les écritures devront déclarer avant tag_attach. Valeurs tag_value_type :
# bool/text/number/date/list/range.
TAG_REGISTRIES: Dict[str, Dict[str, tuple]] = {
    "model_official": {
        "release_date": ("date", "date de conception"),
        "developer": ("text", "développeur / organisation"),
        "architecture": ("text", "architecture du modèle"),
        "parameter_count": ("text", "nombre de paramètres"),
        "license": ("text", "licence"),
        "is_open_weights": ("bool", "poids ouverts"),
        "parent_model": ("text", "modèle parent (famille)"),
        "family": ("text", "famille du modèle (ex. gpt, claude-opus, qwen)"),
        "knowledge_cutoff": ("date", "date de cutoff des connaissances"),
        "reasoning_options": ("list", "options de raisonnement : [{type: "
                              "toggle|effort|budget_tokens, values?}]"),
        "interleaved": ("text", "lecture des infos intermédiaires en "
                        "streaming : true ou {field: reasoning_details}"),
        "modalities_input": ("list", "modalités d'entrée (text, image, pdf, "
                             "audio, input_audio…)"),
        "modalities_output": ("list", "modalités de sortie (text, audio, "
                              "output_audio…)"),
        "capability": ("list", "capacités intrinsèques : vision, agentic, "
                        "reasoning, json_mode, embeddings, long_context, "
                        "streaming, tool_use"),
    },
    "provider": {
        "provider_type": ("text", "cloud/local/ollama/builtin"),
        "api_type": ("text", "SDK par défaut (openai_compatible, anthropic…)"),
        "npm": ("text", "package npm du provider (ex. @ai-sdk/openai)"),
        "env": ("list", "variables d'environnement requises"),
        "website": ("text", "site web"),
        "is_free_tier_provider": ("bool", "provider gratuit (free tier)"),
        "capability": ("list", "capacités offertes par le provider"),
    },
    "endpoint": {
        "api_type": ("text", "le SDK : openai_compatible, anthropic, gemini, "
                      "bedrock…"),
        "label": ("text", "libellé (v1, v1beta, region, resource…)"),
        "region": ("text", "région (optionnel)"),
        "template": ("text", "url templatée (ex. https://{region}-… )"),
        "is_default": ("bool", "endpoint par défaut"),
    },
    "provider_endpoint": {
        "is_default": ("bool", "endpoint par défaut du provider"),
        "priority": ("number", "ordre de préférence"),
        "region": ("text", "région servie (optionnel)"),
    },
    "model_provider_endpoint": {
        "provider_model_name": ("text", "nom du modèle CHEZ le provider"),
        "context_window_tokens": ("number", "limite provider : contexte"),
        "input_limit_tokens": ("number", "limite provider : entrée max "
                               "(si différente du contexte)"),
        "max_output_tokens": ("number", "limite provider : sortie max"),
        "status": ("text", "status/lifecycle SOURCÉ (active/deprecated/"
                   "experimental/beta — vient de la source)"),
        "region": ("text", "variante régionale du modèle (suffixe @eu…)"),
        "free_tier": ("bool", "modèle gratuit chez ce provider"),
        "typekey": ("text", "type de clé porté par l'id (:free, :thinking…)"),
        "route_npm": ("text", "package npm requis pour ce modèle chez le "
                      "provider (routing)"),
        "route_api": ("text", "url API spécifique à ce modèle (routing)"),
        "route_shape": ("text", "shape de requête : completions|responses"),
        "experimental_modes": ("list", "modes expérimentaux : [{mode, cost, "
                               "body, headers}] — ex. fast (service_tier…"),
        "capability": ("list", "capacités PROPRES au lien (agentic chez ce "
                        "provider, variante vision…)"),
    },
    "provider_typekey": {
        "free_tier": ("bool", "type de clé gratuit (free tier)"),
        "usage_scope": ("text", "scope d'usage : requests/tokens/cost/latency"),
        "priority": ("number", "ordre de préférence entre types de clés"),
        "label": ("text", "libellé lisible du type de clé"),
        "source_suffix": ("text", "suffixe d'id de la source (ex. :free, "
                          ":thinking, :120b)"),
    },
    "model_provider_endpoint_typekey": {
        "capacity_tokens": ("number", "capacité effective (contexte)"),
        "cost_in_per_1m": ("number", "coût entrée /1M tokens"),
        "cost_out_per_1m": ("number", "coût sortie /1M tokens"),
        "cost_think_per_1m": ("number", "coût thinking /1M tokens"),
        "cache_read_per_1m": ("number", "coût cache lecture /1M tokens"),
        "cache_write_per_1m": ("number", "coût cache écriture /1M tokens"),
        "cost_input_audio_per_1m": ("number", "coût audio entrée /1M tokens"),
        "cost_output_audio_per_1m": ("number", "coût audio sortie /1M tokens"),
        "cost_tiers": ("list", "coûts par tranche de contexte : [{input, "
                       "output, cache_read, tier:{type, size}}]"),
        "typekey_type": ("text", "type de clé (base, free, thinking…)"),
        "free_tier": ("bool", "gratuit pour ce (modèle, endpoint, typekey)"),
        "quota_req_per_min": ("number", "quota : requêtes/minute"),
        "quota_tok_per_min": ("number", "quota : tokens/minute"),
        "quota_tok_per_day": ("number", "quota : tokens/jour"),
        "quota_cost_per_day": ("number", "quota : coût/jour"),
    },
}


def seed_catalogue_types(db: Db, token: str = "") -> Dict[str, Any]:
    """Déclare les 7 data_types catalogue LLM + registres de tags + source
    models.dev. IDEMPOTENT — appelable à tout moment (init, reset, re-seed).

    Ne TOUCHE pas aux données (pas d'install de data) : l'init viendra de
    models.dev via le buffer.
    """
    db.check_write(token or db._write_token)
    lwrite.add_source(db, "distant", SOURCE_MODELS_DEV,
                      "modèles/providers/endpoints depuis models.dev", token=token)
    created: Dict[str, str] = {}
    registries = 0
    for type_ in CATALOGUE_ORDER:
        spec = CATALOGUE_TYPES[type_]
        r = data_table.create_new_data_type(
            db, type_, description=spec["description"],
            extra_cols=spec["extra_cols"], token=token)
        created[type_] = r["status"]
        dt = data_table.get_table(db, type_)
        for tag_type, (vt, desc) in TAG_REGISTRIES.get(type_, {}).items():
            dt.tag_type_add(tag_type, tag_value_type=vt, description=desc,
                            token=token)
            registries += 1
    return {"ok": True, "types": created, "tag_registries": registries,
            "source": SOURCE_MODELS_DEV}