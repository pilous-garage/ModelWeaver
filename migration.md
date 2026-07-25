# Migration Architecture — Daemon Routes Namespacing & Module Interfaces

> **Version cible** : daemon 0.8.1+  
> **Branch** : `test-npm-dev`  
> **Statut** : Phase 1 en cours  
> **Dernière mise à jour** : 2026-07-25

---

## Vision d'ensemble

Réorganiser `services/api/daemon.py` (2231 lignes, monolithique) en une architecture propre :

```
Client (GUI/CLI)
  ↓ HTTP /v1/<namespace>/<action>
daemon.py (routeur fin — ne fait que router)
  ↓ dispatch()  [appel intra-processus, pas HTTP]
services/<name>/service.py  (couche de service)
  ↓
modules/<name>/<name>_module.py  (interface publique du module)
```

**Principes** :
- Le daemon n'a **aucune import direct** depuis `modules/` — délègue toujours via des services
- Chaque module a un fichier `_module.py` qui expose sa surface publique
- Chaque service a `_contract/interface.py` + `service.py`
- Les routes sont enregistrées dynamiquement via `router.register()`
- `dispatch()` permet aux services d'appeler les routes daemon **sans HTTP**
- Les routes runtime (`service/register`, `service/unregister`) utilisent le même système

---

## Phase 1 — Infrastructure : `services/api/router.py`

| # | Tâche | Détail | Statut |
|---|-------|--------|--------|
| 1.1 | Créer `services/api/router.py` | Nouveau module : `ROUTES`, `register()`, `dispatch()`, `get_route()` | 🔴 À faire |
| 1.2 | Migrer le dict `ROUTES` de `daemon.py` → `router.py` | Toutes les 108 routes | 🔴 À faire |
| 1.3 | Migrer le dict `STREAMING_ROUTES` → `router.py` | 1 entrée (`llm/chat/stream`) | 🔴 À faire |
| 1.4 | Remplacer `ROUTES.get(route)` dans `MWAPIHandler` par `router.dispatch(route)` | `daemon.py` devient thin | 🔴 À faire |
| 1.5 | Remplacer `STREAMING_ROUTES.get(route)` par `router.get_streaming(route)` | Même pattern | 🔴 À faire |
| 1.6 | `daemon.py` fait `from services.api.router import ROUTES, STREAMING_ROUTES, register` | Import propre | 🔴 À faire |
| 1.7 | Vérifier que tous les tests passent | `cargo test`, tests Python | 🔴 À faire |

**Fichiers modifiés** :
- `services/api/router.py` (nouveau)
- `services/api/daemon.py` (réduire fortement)

---

## Phase 2 — Namespacing des routes

### 2.1 Système de namespaces

Les routes utilisent le pattern `<namespace>/<action>` :

| Namespace | Routes actuelles | Nombre |
|-----------|------------------|--------|
| `system` | `info`, `deps/check`, `state/get`, `state/save` | 4 |
| `db` | `init`, `check`, `versions` | 3 |
| `catalogue` | `tools/list`, `seed`, `sync`, `tools_table/update`, `fetch/remote`, `skills/*` (8), `behaviors/*` (8), `personalities/*` (8), `roles/*` (8), `agents/*` (8), `all` | 27 |
| `tools` | `installed/list`, `install`, `uninstall`, `install/all` | 4 |
| `jobs` | `add`, `list`, `status`, `cancel`, `clear` | 5 |
| `deps` | `check`, `install`, `install_target`, `check_manifest` | 4 |
| `keys` | `set`, `get`, `list`, `delete`, `set_lock`, `onboard` | 6 |
| `providers` | `list` | 1 |
| `provider` | `endpoint/add` | 1 |
| `llm` | `models/list`, `recommend`, `chat`, `chat/stream`, `capabilities`, `bridge/status`, `context/probe`, `context/history`, `local/*` (4) | 12 |
| `auth` | `info` | 1 |
| `logs` | `read`, `write` | 2 |
| `agent` | `list`, `get`, `create`, `delete`, `execute`, `manager/status`, `resources/evaluate`, `admit`, `signal*`, `signals`, `signal/ack`, `signal/complete`, `stream`, `spawn`, `handoff`, `launch`, `metrics` | 17 |
| `service` | `list`, `restart`, `stop`, `resources` | 4 |
| `chat` | `session/*` (9) | 9 |
| `tarif` | `info`, `sync` | 2 |
| `usage` | `budget`, `free_tier` | 2 |
| `lib` | `list`, `resolve`, `scan` | 3 |
| **Total** | | **108** |

### 2.2 Enregistrement par namespace

Chaque namespace a une fonction d'enregistrement :

```python
# router.py
def register_system_routes(): ...
def register_agent_routes(): ...
def register_service_routes(): ...
def register_llm_routes(): ...
# ... unregister per group
```

Chaque appel utilise `router.register("namespace/action", handler)`.

### 2.3 `dispatch()` pour appels intra-processus

```python
# Depuis n'importe quel service
from services.api.router import dispatch

result = dispatch("service/list", {})        # appel direct, pas HTTP
result = dispatch("agent/metrics", {"agent_id": 123})
```

---

## Phase 3 — Services manquants

### 3.1 `services/sql/` — wrapper pour `modules/sql/db.py`

**Module cible** : `modules/sql/db.py`  
**Interface** : `modules/sql/sql_module.py` (nouveau)  
**Service** : `services/sql/service.py` + `_contract/interface.py`

| Route daemon | Handler actuel | Appelle directement | Après migration appelle |
|-------------|---------------|--------------------|------------------------|
| `db/init` | `op_db_init` | `modules.sql.db` | `services.sql.init()` |
| `db/check` | `op_db_check` | `modules.sql.db` | `services.sql.check()` |
| `db/versions` | `op_db_versions` | `read_db_version` (module) | `services.sql.get_versions()` |

**Tâches** :
| # | Tâche | Statut |
|---|-------|--------|
| 3.1.1 | Créer `modules/sql/sql_module.py` (interface publique) | 🔴 |
| 3.1.2 | Créer `services/sql/_contract/interface.py` | 🔴 |
| 3.1.3 | Créer `services/sql/service.py` | 🔴 |
| 3.1.4 | Migrer `op_db_init` → délègue à `services.sql` | 🔴 |
| 3.1.5 | Migrer `op_db_check` → délègue à `services.sql` | 🔴 |
| 3.1.6 | Migrer `op_db_versions` → délègue à `services.sql` | 🔴 |
| 3.1.7 | Retirer imports directs `modules.sql.db` depuis `daemon.py` | 🔴 |

### 3.2 `services/key_manager/` — wrapper pour `modules/key_manager/`

**Module cible** : `modules/key_manager/key_manager.py`, `modules/key_manager/onboarder.py`  
**Service** : `services/key_manager/service.py`

| Route daemon | Handler actuel | Appelle directement | Après migration appelle |
|-------------|---------------|--------------------|------------------------|
| `keys/set` | `op_keys_set` | `KeyManager` (module) | `services.key_manager.set_key()` |
| `keys/get` | `op_keys_get` | `KeyManager`, `KeyLockedError` (module) | `services.key_manager.get_key()` |
| `keys/list` | `op_keys_list` | `KeyManager` (module) | `services.key_manager.list_keys()` |
| `keys/delete` | `op_keys_delete` | `KeyManager` (module) | `services.key_manager.delete_key()` |
| `keys/set_lock` | `op_keys_set_lock` | `KeyManager` (module) | `services.key_manager.set_lock()` |
| `keys/onboard` | `op_keys_onboard` | `Onboarder` (module) | `services.key_manager.onboard()` |

**Tâches** :
| # | Tâche | Statut |
|---|-------|--------|
| 3.2.1 | Créer `modules/key_manager/key_manager_module.py` (interface) | 🔴 |
| 3.2.2 | Créer `services/key_manager/_contract/interface.py` | 🔴 |
| 3.2.3 | Créer `services/key_manager/service.py` | 🔴 |
| 3.2.4 | Migrer 6 handlers op_keys_* | 🔴 |
| 3.2.5 | Retirer imports directs `modules.key_manager.*` depuis `daemon.py` | 🔴 |

### 3.3 `services/usage/` — wrapper pour `modules/usage/`

**Module cible** : `modules/usage/budget.py`  
**Service** : `services/usage/service.py`

| Route daemon | Handler actuel | Appelle directement | Après migration appelle |
|-------------|---------------|--------------------|------------------------|
| `usage/budget` | `op_usage_budget` | `get_budget_summary`, `get_budget_rows` (module) | `services.usage.get_budget()` |
| `usage/free_tier` | `op_usage_free_tier` | `get_free_tier_models` (module) | `services.usage.get_free_tier()` |

### 3.4 `services/system/` — wrapper pour `modules/system/`

**Module cible** : `modules/system/deps.py`  
**Service** : `services/system/service.py`

| Route daemon | Handler actuel | Appelle directement | Après migration appelle |
|-------------|---------------|--------------------|------------------------|
| `deps/check` | `op_deps_check` | `check_all_units` (via `services.depends`) | `services.system.check_deps()` |
| `deps/install` | `op_deps_install` | `install_system_package` (module) | `services.system.install_dep()` |
| `deps/install_target` | `op_deps_install_target` | `install_target_dependencies` (module) | `services.system.install_target()` |
| `deps/check_manifest` | `op_deps_check_manifest` | `deps_mod` (via module) | `services.system.check_manifest()` |

### 3.5 `services/llm_manager/` — wrapper pour `modules/llm_manager/`

**Modules cibles** : `modules/llm_manager/llm_manager.py`, `modules/llm_manager/litellm_bridge.py`, `modules/llm_manager/local_engines.py`  
**Service** : `services/llm_manager/service.py`

| Route daemon | Handler actuel | Appelle directement | Après migration appelle |
|-------------|---------------|--------------------|------------------------|
| `llm/models/list` | `op_llm_models_list` | `LLMManager`, `seed_providers` (module) | `services.llm_manager.list_models()` |
| `llm/recommend` | `op_llm_recommend` | `LLMManager` (module) | `services.llm_manager.recommend()` |
| `llm/chat` | `op_llm_chat` | `LiteLLMBridge` (module) | `services.llm_manager.chat()` |
| `llm/chat/stream` | `op_llm_chat_stream_sse` | `LiteLLMBridge` (module) | `services.llm_manager.chat_stream()` |
| `llm/capabilities` | `op_llm_capabilities` | `LiteLLMBridge` (module) | `services.llm_manager.capabilities()` |
| `llm/bridge/status` | `op_llm_bridge_status` | `BaseBridge` (module) | `services.llm_manager.bridge_status()` |
| `llm/context/probe` | `op_llm_context_probe` | `LiteLLMBridge` | `services.llm_manager.context_probe()` |
| `llm/context/history` | `op_llm_context_history` | `litellm_bridge` | `services.llm_manager.context_history()` |
| `llm/local/list` | `op_llm_local_list` | `get_local_engine_manager` (module) | `services.llm_manager.local_list()` |
| `llm/local/start` | `op_llm_local_start` | `get_local_engine_manager` | `services.llm_manager.local_start()` |
| `llm/local/stop` | `op_llm_local_stop` | `get_local_engine_manager` | `services.llm_manager.local_stop()` |
| `llm/local/models` | `op_llm_local_models` | `get_local_engine_manager` | `services.llm_manager.local_models()` |

### 3.6 `services/checker/` — wrapper pour `modules/checker/`

**Module cible** : `modules/checker/checker.py`  
**Service** : `services/checker/service.py`

### 3.7 `services/config/` — wrapper pour `modules/config/`

**Module cible** : `modules/config/config_manager.py`  
**Service** : `services/config/service.py`

### 3.8 `services/container_manager/` — wrapper pour `modules/container_manager/`

**Module cible** : `modules/container_manager/container_manager.py`  
**Service** : `services/container_manager/service.py`

### 3.9 `services/dashboard/` — wrapper pour `modules/dashboard/`

**Module cible** : `modules/dashboard/dashboard.py`  
**Service** : `services/dashboard/service.py`

### 3.10 `services/installer/` — wrapper pour `modules/installer/`

**Modules cibles** : `modules/installer/installer.py`, `modules/installer/recipe_parser.py`, `modules/installer/github_bridge.py`  
**Service** : `services/installer/service.py`

### 3.11 `services/organiser/` — wrapper pour `modules/organiser/`

### 3.12 `services/plumber/` — wrapper pour `modules/plumber/`

### 3.13 `services/test_runner/` — wrapper pour `modules/test_runner/`

---

## Phase 4 — Interfaces `_module.py`

Pour chaque module `modules/<name>/`, créer `<name>_module.py` qui :
- Réexporte uniquement les symboles publics listés dans `_contract/interface.py`
- Masque les internals (fonctions utilitaires, imports privés)
- Sert de contrat public pour les services

Exemple : `modules/sql/sql_module.py`

```python
"""Interface publique du module `sql`."""
from modules.sql.db import ModelWeaverDB, CatalogueDB, RuntimeDB, AgentsDB

__all__ = ['ModelWeaverDB', 'CatalogueDB', 'RuntimeDB', 'AgentsDB']
```

| # | Module | Fichier `_module.py` | Statut |
|---|--------|---------------------|--------|
| 4.1 | `sql` | `modules/sql/sql_module.py` | 🔴 |
| 4.2 | `key_manager` | `modules/key_manager/key_manager_module.py` | 🔴 |
| 4.3 | `llm_manager` | `modules/llm_manager/llm_manager_module.py` | 🔴 |
| 4.4 | `usage` | `modules/usage/usage_module.py` | 🔴 |
| 4.5 | `system` | `modules/system/system_module.py` | 🔴 |
| 4.6 | `checker` | `modules/checker/checker_module.py` | 🔴 |
| 4.7 | `config` | `modules/config/config_module.py` | 🔴 |
| 4.8 | `container_manager` | `modules/container_manager/container_manager_module.py` | 🔴 |
| 4.9 | `dashboard` | `modules/dashboard/dashboard_module.py` | 🔴 |
| 4.10 | `installer` | `modules/installer/installer_module.py` | 🔴 |
| 4.11 | `organiser` | `modules/organiser/organiser_module.py` | 🔴 |
| 4.12 | `plumber` | `modules/plumber/plumber_module.py` | 🔴 |
| 4.13 | `catalogue` | `modules/catalogue/catalogue_module.py` | 🔴 |
| 4.14 | `test_runner` | `modules/test_runner/test_runner_module.py` | 🔴 |
| 4.15 | `utils` | `modules/utils/utils_module.py` | 🔴 |

---

## Phase 5 — Refactor des handlers `daemon.py`

### 5.1 Objectif

`daemon.py` passe de 2231 lignes (tout-en-un) à un routeur fin de ~300 lignes.

### 5.2 État actuel des imports directs à retirer de `daemon.py`

| Import direct | Destinations | Actions affectées | Remplacement |
|--------------|-------------|-------------------|-------------|
| `from modules.sql.db import ModelWeaverDB, CatalogueDB, RuntimeDB, AgentsDB, ...` | 6+ symboles | `op_db_init`, `op_db_check`, `op_db_versions`, `op_agent_list`, `op_agent_create`, `op_agent_delete`, `op_agent_get`, `op_agent_execute`, `op_agent_launch`, `op_keys_*`, `op_usage_*`, `op_chat_session_*`, `op_service_list`, etc. | `services.sql.module`, `services.key_manager.module`, `services.usage.module` |
| `from modules.checker.checker import Checker` | 1 | Utilisé inline | `services.checker.module` |
| `from modules.llm_manager.litellm_bridge import LiteLLMBridge` | LiteLLMBridge | `op_llm_chat`, `chat/stream`, `context/probe`, `context/history`, bridge status | `services.llm_manager.module` |
| `from modules.llm_manager.llm_manager import LLMManager, seed_providers/seed_models/seed_provider_models` | 4+ | `op_llm_models_list`, `op_llm_recommend`, init | `services.llm_manager.module` |
| `from modules.llm_manager.local_engines import get_local_engine_manager` | 1 | `op_llm_local_*` (4 routes) | `services.llm_manager.module` |
| `from modules.key_manager.key_manager import KeyManager` | 1 | `op_keys_get` (inline use) | `services.key_manager.module` |
| `from modules.key_manager.onboarder import Onboarder` | 1 | `op_keys_onboard` | `services.key_manager.module` |
| `from modules.usage.budget import get_budget_summary, get_budget_rows, get_free_tier_models` | 3 | `op_usage_budget`, `op_usage_free_tier` | `services.usage.module` |
| `from modules.system.deps import install_system_package, install_target_dependencies` | 2 | `op_deps_install`, `op_deps_install_target` | `services.system.module` |
| `from modules.llm_manager.base_bridge import BridgeError, ErrorCategory` | 2 | `op_llm_chat` (exception handling) | `services.llm_manager.module` |
| `from modules.sql.db import _ensure_classes_outils_table, resolve_classe_id, _default_class_for_ref` | 3 | divers | `services.sql.module` |

### 5.3 Tâches

| # | Tâche | Statut |
|---|-------|--------|
| 5.1 | Retirer imports directs `modules.sql.db` | 🔴 |
| 5.2 | Retirer imports directs `modules.llm_manager.*` | 🔴 |
| 5.3 | Retirer imports directs `modules.key_manager.*` | 🔴 |
| 5.4 | Retirer imports directs `modules.usage.budget` | 🔴 |
| 5.5 | Retirer imports directs `modules.system.deps` | 🔴 |
| 5.6 | Retirer imports directs `modules.checker.checker` | 🔴 |
| 5.7 | Remplacer chaque usage par délégation service | 🔴 |
| 5.8 | Vérifier `daemon.py` = < 400 lignes après nettoyage | 🔴 |
| 5.9 | Tests passent | 🔴 |

---

## Phase 6 — Routes dynamiques runtime (Agent-as-Service)

### 6.1 Routes à ajouter

| Route | Méthode | Handler | Description |
|-------|---------|---------|-------------|
| `service/register` | POST | `op_service_register` | Enregistre un agent comme service supervisé |
| `service/unregister` | DELETE | `op_service_unregister` | Retire un agent-as-service du superviseur |
| `service/<name>/start` | POST | `op_service_start_dynamic` | Démarre un service dynamique enregistré |
| `service/<name>/stop` | POST | `op_service_stop_dynamic` | Stoppe un service dynamique |
| `service/<name>/status` | GET | `op_service_status_dynamic` | Statut d'un service dynamique |

### 6.2 Mécanisme

1. `POST /v1/service/register` avec `{name, command, agent_id, mode}` 
2. Handler vérifie l'agent existe, crée un `ServiceInfo`
3. Appelle `router.register_dynamic("service/<name>", handler)` 
4. Écrit dans `runtime.db.services` la nouvelle entrée
5. Rust supervisor lit `runtime.db.services` et gère le process
6. `DELETE /v1/service/<name>` inverse le processus

### 6.3 Tâches

| # | Tâche | Statut |
|---|-------|--------|
| 6.1 | `op_service_register(params)` | 🔴 |
| 6.2 | `op_service_unregister(params)` | 🔴 |
| 6.3 | Registration dans `ROUTES` au runtime | 🔴 |
| 6.4 | `router.register_dynamic()` pour runtime routes | 🔴 |
| 6.5 | Synchronisation avec `runtime.db.services` | 🔴 |
| 6.6 | Tests dynamiques | 🔴 |

---

## Phase 7 — Vérification finale

| # | Vérification | Statut |
|---|-------------|--------|
| 7.1 | Aucun import direct `modules/` dans `daemon.py` | 🔴 |
| 7.2 | Toutes les routes sont enregistrées via `router.register()` | 🔴 |
| 7.3 | `dispatch()` fonctionne pour tous les namespaces | 🔴 |
| 7.4 | `cargo build --release` propre (si impact Rust) | 🔴 |
| 7.5 | `pytest` ou tests équivalents passent | 🔴 |
| 7.6 | GUI/CLI fonctionnent via HTTP | 🔴 |
| 7.7 | Documentation `migration.md` à jour | ✅ |

---

## Progress Summary

| Phase | Tâches | Terminé |
|-------|--------|---------|
| 1 — `router.py` | 7 | 0/7 |
| 2 — Namespacing | ~22 namespaces + dispatch | 0 |
| 3 — Services manquants | 13 services, ~70 tâches | 0 |
| 4 — `_module.py` interfaces | 15 modules | 0 |
| 5 — Refactor daemon.py handlers | 9 tâches | 0 |
| 6 — Routes dynamiques | 6 tâches | 0 |
| 7 — Vérification finale | 7 tâches | 0 |
| **Total** | **~138 tâches** | **0** |

---

## Fichier de référence

- **daemon routes actuelles** : `services/api/daemon.py:1806-1938` (dict `ROUTES`)
- **daemon streaming routes** : `services/api/daemon.py:1939-1941` (dict `STREAMING_ROUTES`)
- **handler count** : 69 fonctions `op_*` dans `daemon.py`
- **modules touchés par imports directs** : 18 modules (voir analyse)
