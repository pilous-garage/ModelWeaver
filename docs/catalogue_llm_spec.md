# Spec — Catalogue LLM (block 1 : LLM) — local_catalogue v4

> Décisions validées 2026-08-18. Remplace le catalogue LLM historique
> (`catalogue_schema.sql` : ~25 tables de référence) par **7 data_types**
> versionnés multi-sources dans `sqlite/local` (V4). Init par `models.dev`
> via le buffer. Blocks suivants : outils, benchmarks, scoring, budgets.

## Principes

1. **Simplification** : tout ce qui est *référence* devient une data_type
   locale V4 (`{type}_data` + `{type}_tag` + `{type}_tag_type` +
   `{type}_source_and_sharing`) — versionné par quad
   `(namespace, name, source, version)`.
2. **Init** : `models.dev` (et déclarations officielles) → dépôt `buffer`
   (`push_out`) → consumer IN du writer local (`consume_buffer`, mini-batch).
3. **Pas de JSON tags** : les capacités sont des **tags relationnels**
   (`add_tag` de la data_type), jamais des champs JSON.
4. **Source** : le `status`/lifecycle est de la **data sourcée** (champ
   fourni par la source, généralement `official` ou `models.dev`). La vraie
   vie des adresses (erreurs, backoff, available…) vit dans le domaine
   **adresse_llm** (séparé).
5. **Writer dédié** : `write_catalogue` est le seul à écrire les 7 data_types
   (mini-batch, WAL). Consommation (scoring, budgets) = domaines séparés.

## 1. Les 7 data_types (block LLM)

### 1.1 `model_official` — le modèle RÉEL (déclaration officielle, type HuggingFace)
- `name`/ref canonique, `developer`, `release_date` (date de conception),
  `architecture`, `parameter_count`, `modality`, `license`, `parent_model_ref`.
- **Tags** : capacités intrinsèques du modèle réel (vision, reasoning, agentic
  officiel, json_mode, embeddings, contexte long…).
- **SANS** les limites/ajustements provider (ceux-ci vivent dans
  `model_provider_endpoint`).
- Source : `official` / `models.dev` / HF.

### 1.2 `provider` — le fournisseur
- `ref`, `name`, `provider_type` (`cloud`/`local`/`ollama`/`builtin`),
  `website`, `is_free_tier_provider`, `api_type` par défaut.
- Source : `models.dev` / `official`.

### 1.3 `endpoint` — l'endpoint API
- `ref`, `label`, `endpoint_url` (avec templates région/resource), `api_type`
  (**le SDK** : openai_compatible, anthropic, gemini, bedrock… → drive le
  routing), région optionnelle.
- Source : `official` / `models.dev`.

### 1.4 `provider_endpoint` — quel provider utilise quel endpoint (N:M)
- `provider_id`, `endpoint_id`, `is_default` (par provider), ordre/priorité.
- Source : `official` / `models.dev`.

### 1.5 `model_provider_endpoint` — le modèle CHEZ (provider × endpoint)
- `provider_model_name` (le nom chez le provider, ex
  `deepseek-ai/deepseek-v4-flash-0731`),
- **`model_official_id`** (colonne ajoutée : lien vers le modèle officiel —
  c'est LA réconciliation, l'ancien `alias_model` disparaît),
- `context_window_tokens`, `max_output_tokens` (les **limites du provider**),
- `status`/lifecycle **sourcé** (champ de la source : official/models.dev),
- **Tags** : capacités propres au lien (agentic dispo chez ce provider,
  variante vision…), via `add_tag`.
- Absorbe l'ancien `provider_models` (+ la partie déclarative de
  `provider_models_mapping`).

### 1.6 `provider_typekey` — les types de clés du provider
- `ref` (`free`/`plus`/`premium`/`''` générique), `description`, ordre de
  préférence.
- Source : `official` / `models.dev`.

### 1.7 `model_provider_endpoint_typekey` — capacité + coût + quota (UNE data_type)
Par `(model_provider_endpoint × provider_typekey)` :
- **capacity** : capacité effective (contexte, tokens),
- **cost** : coûts in/out/thinking, `free_tier` flag,
- **quota** : limites de taux (req/min, tok/min, tok/day…).
- C'est de la **data catalogue** (pas runtime). Source : `models.dev` /
  `official` / enrichissements.

## 2. Domaines voisins (HORS catalogue LLM)

| domaine | contenu | statut |
|---|---|---|
| `adresse_llm` (séparé) | adresses = (provider, endpoint, model_provider_endpoint, typekey) + status/lifecycle runtime + état (erreur, backoff, available, first/last_use) | plus tard |
| `scoring` (domaine propre, écrivain dédié) | scores d'expérience (domaine×niveau, task_type×niveau, thinking_power…) | plus tard |
| `budgets` / consommations (domaine propre) | budgets consommés, usage par adresse | plus tard |
| `benchmark_model` (data_type) | scores scrapés par source/version (remplace `model_benchmarks_raw`, `model_efficacy`) | plus tard (scrapers) |
| block **outils** | `classes_outils`, `catalogue_outils`, `catalogue_versions`, `catalogue_recettes`, `outils_popularite`, `catalogue_commands` | block 2 |

## 3. RAM / `release_ram` (data types génératrices)

Les data_types qui servent à **GÉNÉRER** d'autres tables ne restent pas en
permanence en RAM — elles sont chargées à l'init de génération ou sur des
ticks très longs :

- data_types concernées : `model_official`, `provider`, `endpoint`,
  `provider_endpoint`, `provider_typekey` (références pures, peu volumineuses
  mais nombreuses) — à confirmer au cas par cas ; les data_types "produites"
  (`model_provider_endpoint`, `model_provider_endpoint_typekey`) sont lues par
  les domaines consommateurs (adresse_llm, scoring, budgets) et peuvent aussi
  être `release_ram`-ées.
- **API** : chaque data_type RAM-portée expose `release_ram()` :
  1. si dirty (entrées modifiées/ajoutées non persistées) → flush HDD via le
     writer local (`write_catalogue`) ;
  2. libère la mémoire (drop des entrées RAM, seuls les index légers
     namespace/name survivent) ;
  3. rechargement paresseux au prochain accès (cache miss → re-read HDD).
- Garantie : `release_ram()` ne **perd jamais de data** — si non sauvegardée,
   elle l'est avant libération.

## 4. Flux d'init

```
models.dev / déclarations officielles
   → buffer.write.push_out (data_type + payload, external_tag)
   → local.write.consume_buffer (mini-batch, token write_catalogue)
   → {type}_data v4 (quad) + tags + source_and_sharing
   → génération des data_types dérivées (model_provider_endpoint*)
   → release_ram() des références pures
```
