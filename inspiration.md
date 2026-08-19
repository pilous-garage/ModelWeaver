# Sources d'inspiration référencées dans ModelWeaver

Ce fichier liste les projets/données externes dont ModelWeaver s'inspire
ou réutilise les données. Référence CLAIRE par projet, avec le périmètre
réutilisé et le mode d'intégration.

---

## models.dev — base de données ouverte des modèles IA

**Référence** : https://models.dev — repo GitHub : `sst/models.dev`
(MIT, ~6.4k stars, projet de l'équipe SST/Anomaly — **même équipe qu'opencode**,
mais projet SÉPARÉ : models.dev est la base de données, opencode en est un client).

**Source de données** : API publique `https://models.dev/api.json`
(~189 providers, ~6690 modèles, ~3.9 MB, régénérée depuis des TOML commités
dans le repo, contributions communautaires validées par CI).

### Ce que models.dev fournit (par provider)
- `id`, `name`, `doc`, `npm` (paquet SDK : `@ai-sdk/openai-compatible`, `@ai-sdk/anthropic`…),
  `env` (vars d'environnement pour la clé), `api` (base URL quand openai-compatible).
- Les URLs sont EXPLICITES pour ~163 providers ; les ~26 restants
  (openai, anthropic, google, azure, bedrock, vertex…) sont SDK-natifs
  (URL par défaut du SDK, ou templatée `{region}`/`{resource}`).

### Ce que models.dev fournit (par modèle)
- `limit.context` (fenêtre de contexte) + `limit.output` (max sortie) — 98-100 % couverts.
- `cost` : `input` / `output` / `cache_read` (USD par million de tokens) — 93 %.
- Capacités : `reasoning`, `tool_call`, `attachment` (vision), `structured_output`,
  `temperature`, `modalities` (entrées : text/image/pdf/video/audio).
- `reasoning_options` : niveaux d'effort de raisonnement acceptés (ex. `high`, `max`
  pour Gemini thinking) — sert à générer les variantes de modèle.
- `open_weights`, `release_date`, `knowledge` (date de coupure), `family`.

### Intégration prévue dans ModelWeaver
- Source principale du catalogue providers/endpoints (refonte `catalogue_provider`,
  `catalogue_endpoint`, `catalogue_provider_endpoint`, `catalogue_provider_endpoint_type_key`).
- Remplit `catalogue_models` (spécs), `provider_models` (prix/context/limits),
  `model_capabilities` (capacités officielles).
- Base SAINE des capacités des modèles pour le bridge (choix du modèle selon
  la tâche : reasoning requis, fenêtre de contexte, prix).
- Complétée par nos tables D'EXPÉRIMENTATION propres : scoring, task_level,
  thinking_power (ce que models.dev ne peut pas fournir — la réalité mesurée).
- Ce fichier `inspiration.md` documente la référence utilisée ; le module
  scraper devra citer la source + inclure la licence MIT.

---

## opencode — agent de codage open-source

**Référence** : https://opencode.ai — repo `sst/opencode`.

- Client de models.dev : son catalogue de modèles vient de models.dev.
- Nous a inspirés pour : intégration directe de toutes les clés API (13/15
  de nos providers testés disponibles sans config), gestion des providers
  locaux, concepts de variantes de modèle (reasoning_options).

---

## litellm — couche d'universalité des providers LLM

**Référence** : https://docs.litellm.ai (paquet `litellm` installé).

- FOURNIT : liste des providers supportés (~146), SDK par provider,
  prix/context par modèle dans `model_prices_and_context_window` (cache),
  résolution `get_llm_provider` → `api_base`.
- Utilisé historiquement pour les données de coût (seed `catalogue_schema.sql`,
  `modules/catalogue/populate.py`).
- LIMITE constatée : les URLs par provider ne sont PAS complètes dans le
  paquet (openai/anthropic/azure/gemini non résolus) → models.dev est la
  source de référence préférée pour les endpoints.

---

## Litellm/autres sources historiques

- Cache local `litellm model_prices_and_context_window` → seed des coûts
  des `provider_models` (remplacé progressivement par models.dev).
- OpenRouter API (`api/v1/models`) → tarifs/supplément (~338 modèles),
  marquage free-tier (`modules/catalogue/openrouter.py`).