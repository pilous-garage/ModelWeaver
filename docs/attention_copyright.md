# Attention — Copyright / Origine des bouts de code

Ce fichier liste tous les extraits de code qui s'inspirent directement du code source
d'**opencode** (https://github.com/opencode-ai/opencode), consulté dans le cadre
d'une analyse comparative entre ModelWeaver et opencode.

Aucun code n'est copié mot pour mot. Les reprises sont des **réécritures en Python**
de principes architecturaux observés dans le code TypeScript d'opencode.

---

## Session 2026-07-28 — Adaptation catalogue dynamique + import providers

### Fichier créé : `modules/llm_manager/catalogue_remote.py`

Inspiré de : `packages/core/src/models-dev.ts` (lignes 154-252)

Ce qui est repris :
- Endpoint central unique (`https://models.dev/api.json`) qui agglomère tout le
  catalogue providers + modèles → adapté en endpoint ModelWeaver + fallback offline
- Cache local avec TTL 5min (`Duration.minutes(5)`, ligne 159)
- Refresh silencieux toutes les 60min (`Schedule.spaced("60 minutes")`, ligne 251)
- Écriture atomique via fichier temporaire (lignes 198-207)
- Flag pour désactiver le fetch (`OPENCODE_DISABLE_MODELS_FETCH`, ligne 216)
- Override de l'URL source via variable d'environnement (`OPENCODE_MODELS_URL`, ligne 154)

Différences ModelWeaver :
- Python vs Effect-TS / TypeScript
- `urllib.request` vs `HttpClient` + `HttpClientRequest`
- Stockage en SQLite vs fichier JSON brut
- Utilise un lock fichier (flock) au lieu du `Flock` cross-process d'opencode

### Fichier créé : `modules/llm_manager/bridges/__init__.py`

Inspiré de : `packages/opencode/src/provider/provider.ts` (lignes 107-134)

Ce qui est repris :
- Registry de providers avec chargement dynamique : `BUNDLED_PROVIDERS` map
  `{npm_package → () => import()}` → adapté en `{provider_ref → module_path}`
  avec `importlib.import_module()`
- Fallback vers un provider générique (opencode : `@ai-sdk/openai-compatible`,
  ModelWeaver : `LiteLLMBridge`)
- Cache des instances de providers en mémoire

Différences ModelWeaver :
- Python `importlib` vs `await import()` dynamique
- Architecture module fichier vs package npm
- Pas de `LanguageModelV3` — contrat `BaseBridge` à la place

### Fichier créé : `modules/llm_manager/bridges/openai.py`

Inspiré de : `packages/opencode/src/provider/provider.ts` (lignes 202-209)

Ce qui est repris :
- Factory `createOpenAI(options)` qui retourne un objet avec `languageModel(modelID)`
  → adapté en classe Python `Bridge(BaseBridge)` avec méthode `chat()`

Différences ModelWeaver :
- SDK openai Python vs `@ai-sdk/openai`
- `BaseBridge.chat()` vs `LanguageModelV3`

### Fichier modifié : `modules/llm_manager/llm_manager.py`

Ajout de la méthode ``LLMManager.sync_from_remote()`` qui délègue à
``catalogue_remote.refresh_sync()``. Import du nouveau module.

### Fichier modifié : `modules/llm_manager/llm_manager_module.py`

Export des nouveaux symboles : ``catalogue_fetch``, ``catalogue_sync``,
``BridgeRegistry``.

### Fichier modifié : `modules/llm_manager/litellm_bridge.py`

Documentation mise à jour : LiteLLMBridge n'est plus le bridge officiel
unique mais le fallback par défaut du BridgeRegistry.

---

## Session 2026-07-28 — Agent build / Tool calling system (3 couches)

### Fichier créé : `AgentsCatalogue/lib/llm/tool.py`

Inspiré de : `packages/opencode/src/tool/tool.ts` (lignes 55-169)

Ce qui est repris :
- Contrat ``Tool.Def`` avec ``name, description, parameters, execute``
  → `Def` dataclass avec name, description, parameters, fn
- Registre d'outils ``Registry`` avec add/list/to_openai_tools/execute
- Décorateur ``Tool.define()`` pour enregistrer une fonction comme outil
- Conversion vers le format OpenAI function calling (to_openai_tools)

Différences ModelWeaver :
- Python / dataclasses vs TypeScript / Effect Schema
- Pas de middleware de truncation automatique
- Pas de système de permissions intégré (prévu pour plus tard)

### Fichier créé : `AgentsCatalogue/lib/llm/resolver.py`

Inspiré de : `packages/opencode/src/tool/registry.ts` (lignes 96-248)
            et du bundle resolver existant `workflow/bundles.py`

Ce qui est repris :
- Résolution d'outils depuis plusieurs sources (registry + YAML)
- Fonction ``resolve_tools()`` qui fusionne et formate en OpenAI tools
- Fonction ``make_dispatcher()`` qui route l'exécution vers le bon handler

Différences ModelWeaver :
- Fusionne avec le système existant de skills YAML
- Pas de MCP tools ni de plugin hooks

### Fichier créé : `AgentsCatalogue/lib/llm/loop.py`

Inspiré de : `packages/opencode/src/session/prompt.ts` (lignes 1081-1341,
            runLoop)

Ce qui est repris :
- Boucle itérative : call LLM → tool_calls → execute → feed back → repeat
- Configuration : max_steps, temperature, provider/model, timeout
- Stop conditions : text response (stop), max_steps, error, global_timeout
- Provider fallback sur erreur (auto-reassign)
- Structure de résultat ``LoopResult`` avec signal, output, steps, turns

Différences ModelWeaver :
- Python vs Effect-TS / TypeScript
- Pas de détection de doom loop (appels identiques répétés)
- Pas de gestion de subagent
- Pas de persistance session DB

---

## Session 2026-07-28 — Permission system pour outils par agent

### Fichier créé : `AgentsCatalogue/lib/llm/permission.py`

Inspiré de : `packages/opencode/src/permission/index.ts` (lignes 28-213)

Ce qui est repris :
- Évaluation last-match-wins : dernière règle qui matche l'emporte
- Actions : ``allow``, ``deny``, ``ask``
- Pattern wildcard via ``fnmatch`` (``*``, ``read_*``, ``shell_*``)
- ``PermissionChecker`` avec ``merge()`` pour fusionner règles
- ``visible_tools()`` pour filtrer les outils deny avant envoi au LLM
- Règles chargées depuis config YAML (``from_dict()``)

Différences ModelWeaver :
- Python vs TypeScript + Effect
- Pas de système ``approved`` persistant (ask toujours renouvelé)
- Pas de permissions par pattern de fichier (``*.env``)
- ``ask`` délègue à un handler optionnel plutôt qu'à une TUI

### Fichier modifié : `AgentsCatalogue/lib/llm/tool.py`

- ``Registry.__init__()`` accepte un ``PermissionChecker`` optionnel
- ``to_openai_tools()`` exclut les outils ``deny``
- ``execute()`` vérifie les permissions avant d'exécuter

---

## Règles

1. Si un extrait de code est copié **textuellement** depuis opencode, il est marqué
   en bloc avec le chemin exact du fichier source et les lignes.
2. Les adaptations et réécritures sont décrites en « Ce qui est repris » ci-dessus.
3. Les principes architecturaux généraux (cache, registry, etc.) ne sont pas tracés
   car ils sont des patterns connus et non la propriété d'opencode.
