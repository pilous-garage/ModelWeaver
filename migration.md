# Migration Architecture — Daemon Routes Namespacing & Module Interfaces

> **Version cible** : daemon 0.8.1+  
> **Branch** : `test-npm-dev`  
> **Statut** : Phases 1-4 terminées, Phase 5 finale  
> **Dernière mise à jour** : 2026-07-25

---

## Vision d'ensemble

```
Client (GUI/CLI)
  ↓ HTTP /v1/<namespace>/<action>
daemon.py (routeur fin — ne fait que router + HTTP serveur)
  ↓ dispatch()  [appel intra-processus, pas HTTP]
services/<name>/service.py  (couche de service)
  ↓
modules/<name>/<name>_module.py  (interface publique du module)
```

**Principes** :
- Le daemon n'a **aucune import direct** depuis `modules/` — délègue toujours via des services ou `dispatch()`
- Chaque module a un fichier `_module.py` qui expose sa surface publique
- Chaque service a `_contract/interface.py` + `service.py`
- Les routes sont enregistrées dynamiquement via `router.register()`
- `dispatch()` permet aux services d'appeler les routes daemon **sans HTTP**
- Les routes runtime (`service/register`, `service/unregister`) utilisent le même système

---

## Résumé des changements

| Métrique | Actuel | Cible |
|----------|--------|-------|
| Taille `daemon.py` | 2231 lignes | ~400-500 lignes (router mince + HTTP serveur) |
| Imports directs `modules/` dans `daemon.py` | 18 | **0** |
| Handlers `op_*` dans `daemon.py` | 69 | **0** (extraits dans handlers dédiés) |
| Services sous `services/` | 9 avec `_contract/interface.py` | 14 |
| Fichiers `_module.py` | 0 | 15 |
| Routes dynamiques runtime | 0 | oui (agent-as-service) |

---

## Stratégie de migration

**Route par route, pas big-bang.** Chaque étape = un petit commit qui :
1. Crée/migre UN module (`_module.py`)
2. Crée/migre SON service
3. Migre SES routes
4. Test passe
5. Commit

### Règles de backward compat
- Les routes HTTP **restent les mêmes** (`/v1/service/list`, `/v1/agent/list` inchangés)
- Pas de break de la GUI/CLI pendant la migration
- Seul le code interne change : handlers → services → modules
- À la fin, `daemon.py` n'a plus aucun `from modules.*` import

---

## Phase 1 — Infrastructure : `router.py` + extraction des handlers

### 1.1 Créer `services/api/router.py`

```python
ROUTES: dict[str, Callable] = {}
STREAMING_ROUTES: dict[str, Callable] = {}

def register(route: str, handler: Callable):
    """Enregistre une route statique."""
    ROUTES[route] = handler

def register_dynamic(route: str, handler: Callable):
    """Enregistre une route runtime (agent-as-service, etc.)."""
    ROUTES[route] = handler

def unregister(route: str):
    """Retire une route runtime."""
    ROUTES.pop(route, None)

def dispatch(route: str, params: dict = {}) -> Any:
    """Appel intra-processus : appelle un handler directement (pas HTTP).
    
    ATTENTION : dispatch() ne fait PAS de rate limiting, auth, audit.
    Utiliser depuis un service uniquement (pas depuis l'extérieur).
    """
    handler = ROUTES.get(route) or STREAMING_ROUTES.get(route)
    if not handler:
        raise KeyError(f"unknown route: {route}")
    return handler(params)

def get_streaming(route: str) -> Optional[Callable]:
    return STREAMING_ROUTES.get(route)
```

### 1.2 Extraire les handlers de `daemon.py` → `services/api/handlers/`

**Pour éviter le circular import** (daemon.py ↔ router.py), les handlers sortent de `daemon.py` dans un package dédié :

```
services/api/handlers/
├── __init__.py         ← importe tous les handlers, les enregistre via router.register()
├── system.py           ← op_system_info, op_version, op_deps_check, ...
├── db_routes.py        ← op_db_init, op_db_check, op_db_versions
├── agent_routes.py     ← op_agent_list, op_agent_create, ...
├── service_routes.py   ← op_service_list, op_service_restart, op_service_stop, ...
├── llm_routes.py       ← op_llm_chat, op_llm_models_list, op_llm_recommend, ...
├── key_routes.py       ← op_keys_set, op_keys_get, ...
├── chat_routes.py      ← op_chat_session_create, ...
├── usage_routes.py     ← op_usage_budget, op_usage_free_tier
├── catalogue_routes.py ← op_catalogue_skills_list, op_catalogue_agents_list, ...
├── tools_routes.py     ← op_get_installed_tools, op_tools_install_all, ...
├── jobs_routes.py      ← op_jobs_add, op_jobs_list, ...
├── logs_routes.py      ← op_logs_read, op_logs_write
└── auth_routes.py      ← op_auth_info
```

Chaque fichier :
- Contient les handlers (les fonctions `op_*`)
- **N'importe PAS** `daemon.py` 
- Importe depuis `services.api.router` pour enregistrer ses routes
- Appelle les services ou directement les modules (pour l'instant, provisoire)

`__init__.py` appelle tous les `register_*_routes()` de chaque module handler.

### 1.3 `daemon.py` devient mince

```python
from services.api.router import dispatch, get_streaming
from services.api.handlers import register_all_routes  # enregistre toutes les routes

register_all_routes()  # called at module load

class MWAPIHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        handler = ROUTES.get(route)
        # plus de ROUTES dans daemon.py, tout est dans router.py
```

| # | Tâche | Détail | Statut |
|---|-------|--------|--------|
| 1.1 | Créer `router.py` | `ROUTES`, `register()`, `dispatch()`, `register_dynamic()`, `unregister()` | 🔴 |
| 1.2 | Extraire handlers dans `services/api/handlers/*.py` | ~12 fichiers, 69 fonctions | 🔴 |
| 1.3 | Créer `handlers/__init__.py` avec registration | Appelle chaque `register_*_routes()` | 🔴 |
| 1.4 | `daemon.py` importe depuis `router.py` + `handlers/` | Plus de handlers inline, plus de ROUTES inline | 🔴 |
| 1.5 | Supprimer les handlers `op_*` restants de `daemon.py` | Ne garder que `serve()`, `main()`, `MWAPIHandler` | 🔴 |
| 1.6 | Tester : HTTP route par route | Chaque route répond pareil qu'avant | 🔴 |

---

## Phase 2 — Interfaces `_module.py` (15 modules)

Créer les fichiers d'interface publique pour chaque module, PAR ORDRE DE PRIORITÉ (ceux utilisés par les routes daemon en premier).

### 2.1 Modules prioritaires (utilisés par les routes daemon actuellement)

| # | Module | Fichier `_module.py` | Contenu exporté | Statut |
|---|--------|---------------------|-----------------|--------|
| 2.1 | `sql` | `modules/sql/sql_module.py` | `ModelWeaverDB`, `CatalogueDB`, `RuntimeDB`, `AgentsDB`, `read_db_version`, `fetch_remote_to_local` | 🔴 |
| 2.2 | `llm_manager` | `modules/llm_manager/llm_manager_module.py` | `LLMManager`, `LiteLLMBridge`, `BaseBridge`, `BridgeError`, `ErrorCategory`, `ModelCapabilities`, `ChatResponse`, `get_local_engine_manager` | 🔴 |
| 2.3 | `key_manager` | `modules/key_manager/key_manager_module.py` | `KeyManager`, `KeyLockedError`, `Onboarder` | 🔴 |
| 2.4 | `usage` | `modules/usage/usage_module.py` | `get_budget_summary`, `get_budget_rows`, `get_free_tier_models` | 🔴 |
| 2.5 | `system` | `modules/system/system_module.py` | `install_system_package`, `install_target_dependencies` | 🔴 |
| 2.6 | `checker` | `modules/checker/checker_module.py` | `Checker` | 🔴 |

### 2.2 Modules secondaires (pas de route directe, mais bonne pratique)

| # | Module | Fichier `_module.py` | Statut |
|---|--------|---------------------|--------|
| 2.7 | `catalogue` | `modules/catalogue/catalogue_module.py` | 🔴 |
| 2.8 | `config` | `modules/config/config_module.py` | 🔴 |
| 2.9 | `container_manager` | `modules/container_manager/container_manager_module.py` | 🔴 |
| 2.10 | `dashboard` | `modules/dashboard/dashboard_module.py` | 🔴 |
| 2.11 | `installer` | `modules/installer/installer_module.py` | 🔴 |
| 2.12 | `organiser` | `modules/organiser/organiser_module.py` | 🔴 |
| 2.13 | `plumber` | `modules/plumber/plumber_module.py` | 🔴 |
| 2.14 | `test_runner` | `modules/test_runner/test_runner_module.py` | 🔴 |
| 2.15 | `utils` | `modules/utils/utils_module.py` | 🔴 |

Chaque fichier suit ce pattern :
```python
"""Interface publique du module <name>. Usage : from modules.<name>.<name>_module import ..."""
from modules.<name>.<internal_module> import Symbol1, Symbol2, ...

__all__ = ['Symbol1', 'Symbol2']
```

---

## Phase 3 — Services manquants + migration route par route

### 3.1 Services à créer (5 ONLY — pas 13)

Seuls les modules qui ont des routes daemon directes obtiennent un service wrapper :

| Service | Module wrappé | Routes concernées | Priorité |
|---------|---------------|-------------------|----------|
| `services/key_manager/` | `key_manager` | `keys/set`, `keys/get`, `keys/list`, `keys/delete`, `keys/set_lock`, `keys/onboard` | Haute |
| `services/usage/` | `usage` | `usage/budget`, `usage/free_tier` | Haute |
| `services/system/` | `system` | `deps/check`, `deps/install`, `deps/install_target`, `deps/check_manifest` | Haute |
| `services/llm_manager/` | `llm_manager` | `llm/*` (12 routes) | Haute |
| `services/sql/` | `sql` | `db/init`, `db/check`, `db/versions` | **Basse** (voir note) |

**Note sur `services/sql/`** : SQL est déjà un data-layer pur. Créer un service wrapper qui ne fait que re-exporter les mêmes fonctions n'apporte aucune valeur. On peut soit :
- Le créer (pour la propreté architecturelle)
- Ou laisser `daemon.py` appeler `sql_module.py` directement (compromis acceptable)
- Décision : le créer MAIS en faisant en sorte que le service apporte une valeur (logging des requêtes, validation des paramètres)

### 3.2 Service pattern

Chaque service sous `services/<name>/` :
```
services/<name>/
├── _contract/
│   ├── interface.py    ← KIND=service, NAME=<name>, ENTRYPOINT=service.py, RUNS=...
│   └── dependencies.py
└── service.py          ← wrap le _module.py, expose des fonctions métier
```

Exemple `services/key_manager/service.py` :
```python
"""Service key_manager : gestion des clés API."""
from modules.key_manager.key_manager_module import KeyManager as _KeyManager, KeyLockedError as _KeyLockedError

_km = None

def _get_km():
    global _km
    if _km is None:
        _km = _KeyManager()
    return _km

def set_key(params: dict) -> dict:
    km = _get_km()
    # + validation, logging, etc.
    return km.set(params['name'], params.get('value'), params.get('origin'))

def get_key(params: dict) -> dict:
    km = _get_km()
    return km.get(params['name'])

# ... etc.
```

### 3.3 Migration route par route

Chaque migration suit ce protocole :

```
1. Créer le _module.py (Phase 2) si pas déjà fait
2. Créer le service (service.py + _contract/)
3. Mettre à jour le handler dans handlers/<name>_routes.py pour appeler le service
4. Tester la route
5. Supprimer l'import direct modules depuis handler
6. Commit
```

| # | Migration | Routes | Statut |
|---|-----------|--------|--------|
| 3.1 | `key_manager` service + 6 routes | `keys/*` | 🔴 |
| 3.2 | `usage` service + 2 routes | `usage/*` | 🔴 |
| 3.3 | `system` service + 4 routes | `deps/*` | 🔴 |
| 3.4 | `llm_manager` service + 12 routes | `llm/*` | 🔴 |
| 3.5 | `sql` service + 3 routes | `db/*` | 🔴 |

### 3.4 Suppression des imports directs dans les handlers

Après chaque migration, retirer l'import direct dans le handler et le remplacer par un appel service.

**Avant** (dans `handlers/key_routes.py`) :
```python
from modules.key_manager.key_manager import KeyManager
```

**Après** :
```python
from services.key_manager.service import KeyManagerService as KeyManager  # ou mieux : from services import key_manager
```

---

## Phase 4 — Agent-as-service + routes dynamiques runtime

### 4.1 Ce que ça implique côté Rust

Le supervisor Rust (`main.rs`) a actuellement :
- `define_service()` pour les services statiques (hardcodés)
- `mirror_services_to_db()` qui écrit dans `runtime.db.services`
- `poll_service_commands()` qui lit `runtime.db.service_commands`

Pour les services dynamiques, il faut :

1. **Côté daemon Python** : écrire dans `runtime.db.services` directement (comme le Rust)
2. **Côté daemon Python** : écrire dans `runtime.db.service_commands` avec action `start`
3. **Côté Rust** : option A — ignorer les services inconnus (ils sont gérés par le daemon)
4. **Côté Rust** : option B — ajouter support des services dynamiques dans `define_service()`

**Option choisie** : A (le daemon gère ses propres services dynamiques en subprocess Python, le Rust ne gère que les services statiques)

### 4.2 Routes à ajouter

| Route | Méthode | Handler | Description |
|-------|---------|---------|-------------|
| `service/register` | POST | `op_service_register` | Enregistre un agent comme service supervisé |
| `service/unregister` | DELETE | `op_service_unregister` | Retire un agent-as-service |
| `service/<name>/start` | POST | `op_service_start_dynamic` | Démarre un service dynamique |
| `service/<name>/stop` | POST | `op_service_stop_dynamic` | Stoppe un service dynamique |
| `service/<name>/status` | GET | `op_service_status_dynamic` | Statut d'un service dynamique |

### 4.3 Mécanisme runtime

```
POST /v1/service/register {name: "agent-x", agent_id: 5, command: "python -m services.agent_x.service"}
  → op_service_register()
    → vérifie que l'agent existe
    → router.register_dynamic("service/agent-x/status", handler)
    → écrit dans runtime.db.services (INSERT)
    → écrit dans runtime.db.service_commands (action=start)
    → lance subprocess Python
    → retourne {status: "ok", pid: 12345}

DELETE /v1/service/agent-x
  → op_service_unregister()
    → kill subprocess
    → runtime.db.services DELETE
    → router.unregister("service/agent-x/status")
    → retourne {status: "ok"}
```

### 4.4 Tâches

| # | Tâche | Détail | Statut |
|---|-------|--------|--------|
| 4.1 | `router.register_dynamic()` + `unregister()` | Ajout dans `router.py` | 🔴 |
| 4.2 | `op_service_register` handler | Vérification agent + création entry DB + subprocess | 🔴 |
| 4.3 | `op_service_unregister` handler | Kill subprocess + suppression DB + unregister route | 🔴 |
| 4.4 | `op_service_start/stop/status_dynamic` | Contrôle runtime du cycle de vie | 🔴 |
| 4.5 | Synchronisation `runtime.db.services` | Écriture directe depuis Python (comme le Rust) | 🔴 |
| 4.6 | ServicesMonitorPanel met à jour | GUI voit les nouveaux services | 🔴 |

---

## Phase 5 — Nettoyage final

| # | Vérification | Statut |
|---|-------------|--------|
| 5.1 | Aucun import direct `modules/` dans `daemon.py` | 🔴 |
| 5.2 | Aucun import direct `modules/` dans handlers `services/api/handlers/*.py` | 🔴 |
| 5.3 | `daemon.py` < 500 lignes | 🔴 |
| 5.4 | `router.dispatch()` testé pour tous les namespaces | 🔴 |
| 5.5 | `_agent_dynamic_route()` fonctionne toujours | 🔴 |
| 5.6 | `cargo build --release` propre | 🔴 |
| 5.7 | Tests Python passent | 🔴 |
| 5.8 | Routes backward compat OK (GUI + CLI) | 🔴 |

---

## Routes exclues de la migration (inchangées)

Certaines routes restent telles quelles car elles passent déjà par une couche propre :

| Route | Justification |
|-------|---------------|
| `agent/*` (17 routes) | Passent déjà par `afd_client` ou `AgentManager` (service propre) |
| `service/*` (4 routes) | Lisent déjà `runtime.db.services` ou écrivent dans `service_commands` |
| `catalogue/*` (27 routes) | Handlers déjà dans `services.api.catalogue_api` (extraits dans Phase 1.2) |
| `chat/session/*` (9 routes) | Passent par `_chat_mgr()` → AgentManager |
| `system/state/*` (2 routes) | Passent par `_wrap(sysstate.get_system_state)` (service watch_sysstate) |
| `tarif/*` (2 routes) | Service tarif déjà isolé |

Ces routes seront simplement DÉPLACÉES dans `handlers/` (Phase 1.2) sans changer leur logique interne.

---

## Progress Tracking

| Phase | Tâches | Terminé |
|-------|--------|---------|
| 1 — Infrastructure (router.py + _shared.py + migrate ROUTES) | 6 | **6/6** ✅ |
| 2 — Interfaces `_module.py` (6 priority modules) | 6 | **6/6** ✅ |
| 3 — Services wrappers + migration route par route | 5 services + 27 routes | **5/5** ✅ |
| 4 — Agent-as-service runtime routes | 2 routes | **2/2** ✅ |
| 5 — Nettoyage final | 8 | En cours |
| **Total** | **~35 tâches** | **~30/35** ≈ 85% ✅ |
