# Analyse Laguna — ModelWeaver

> Analyse complète du projet ModelWeaver (V0.7.4) — sécurité, contrats, architecture, améliorations.
> Date : 2026-07-22
> Scope : backend Python, frontend TypeScript/Tauri, documentation, tests.

---

## 1. Vue d'ensemble du projet

### 1.1 Description
ModelWeaver est un **framework d'agents autonomes** basé sur des LLM, avec un IDE visuel (sandbox), un catalogue de skills, un système de collaboration multi-agents (git distribué), et une architecture modulaire backend/frontend découpée.

### 1.2 Architecture
- **Backend** : Daemon Python (HTTP/JSON sur 127.0.0.1), AFD (Agent Framework Daemon) processus dédié, modules/services avec contrats `_contract/`.
- **Frontend** : Tauri (Rust) + React/TypeScript. Deux fenêtres : `main` (dashboard) et `sandbox` (IDE agents).
- **Agent Framework** : `AgentFrameWork/` — FSM interpreter, StreamBus, Factory, Scheduler, Ticker.
- **Agents Catalogue** : `AgentsCatalogue/` — rôles YAML, skills (63+), personalities, behaviors, lib/ (fonctions Python).

### 1.3 Versions majeures
| Version | Fonctionnalité clé |
|---------|-------------------|
| V0.5 | GUI Tauri, architecture 2 apps |
| V0.6 | Key Manager (keyring), LLM Bridge, Agent Framework, FSM, Skills, Sandboxing, Rate limiting |
| V0.7 | GUI modulaire, Sandbox IDE, Graph FSM editor, CatalogTree, Entrypoints agents |

---

## 2. Analyse de sécurité

### 2.1 Vulnérabilités critiques

#### 2.1.1 `exec()` sur code inline de skills (RISK : HIGH)
**Fichiers** : `services/skill_manager.py:167`, `services/depends.py:53,67`

```python
# skill_manager.py:167
exec(inline_code, ns)
```

Le `SkillManager.call()` exécute du code Python fourni via `implementation.code` dans le YAML du skill. Bien que cela soit "sandboxé" par le `FsAuthManager` pour les accès hôte, **le code Python lui-même n'est pas sandboxé** — un skill malveillant ou une injection YAML peut exécuter n'importe quel code Python dans le processus du daemon.

**Impact** : Exécution arbitraire de code si un agent peut modifier un YAML de skill ou si un skill inline contient du code malveillant.

**Recommandation** :
- Supprimer le support `implementation.code` (inline Python) ou le restreindre à un AST validator.
- Utiliser `RestrictedPython` pour exécuter le code inline.
- Auditer tous les YAML de skills pour détecter des `implementation.code`.

#### 2.1.2 `exec()` dans `services/depends.py` (RISK : MEDIUM)
**Fichier** : `services/depends.py:53,67`

```python
exec(compile(iface.read_text(), str(iface), "exec"), ns)
```

Lit et exécute les fichiers `_contract/interface.py` pour extraire `DEPENDS`. Ces fichiers sont dans le repo, donc le risque est limité, mais c'est une pratique fragile.

**Recommandation** : Utiliser `ast.literal_eval` ou parser le fichier avec `ast` pour extraire les constantes au lieu d'exécuter le code.

#### 2.1.3 `shell=True` dans `modules/installer/recipe_parser.py` (RISK : HIGH)
**Fichier** : `modules/installer/recipe_parser.py`

```python
cmd_str, shell=True, stdout=logf, stderr=subprocess.STDOUT
```

Utilisation de `shell=True` avec des commandes construites dynamiquement. Si une recette d'installation contient des caractères spéciaux, cela peut mener à une injection de commandes.

**Recommandation** : Utiliser `shell=False` et passer les arguments en liste. Auditer toutes les recettes.

### 2.2 Vulnérabilités de binding réseau

#### 2.2.1 `catalogue_server.py` bind sur `0.0.0.0` (RISK : HIGH)
**Fichier** : `modules/sql/catalogue_server.py:138`

```python
server = CatalogueServer(("0.0.0.0", args.port), CatalogueAPIHandler)
```

Le serveur catalogue ancien bind sur toutes les interfaces réseau. Bien que le daemon principal bind sur `127.0.0.1` (documenté dans `ARCHITECTURE_API.md:154`), ce serveur legacy est toujours présent.

**Impact** : Toute machine sur le réseau peut accéder au catalogue sans authentification.

**Recommandation** :
- Changer `0.0.0.0` → `127.0.0.1`.
- Déprécier `catalogue_server.py` au profit du daemon principal.

### 2.3 Problèmes d'authentification et d'autorisation

#### 2.3.1 Token d'API non validé sur toutes les routes (RISK : MEDIUM)
**Fichier** : `services/api/daemon.py`

Le daemon utilise un token de session (`~/.modelweaver/api.token`), mais il n'est pas clair que **toutes** les routes le valident. Les routes `auth/info` et `health` sont explicitement exemptées, mais d'autres routes publiques pourraient exister.

**Recommandation** : Auditer chaque route pour s'assurer qu'elle valide le token (sauf les routes explicitement publiques).

#### 2.3.2 `host_run` skill — exécution shell sandboxée mais permissif (RISK : MEDIUM)
**Fichiers** : `AgentsCatalogue/lib/system/fs.py:60-75`, `services/sandbox.py`

La skill `host_run` exécute des commandes shell dans un sandbox avec des limites RLIMIT. Cependant :
- Le sandbox utilise `preexec_fn` (déprécié, remplacé par `process_group` dans Python 3.11+).
- Aucune restriction sur le type de commande (pas de allow-list).
- Le `cwd` peut être n'importe quel chemin autorisé par `FsAuthManager`.

**Recommandation** :
- Migrer de `preexec_fn` vers `process_group`.
- Considérer une allow-list de commandes pour `host_run`.
- Ajouter un timeout plus strict.

### 2.4 Gestion des clés API

#### 2.4.1 Key Manager — bonnes pratiques appliquées (RISK : LOW)
**Fichier** : `modules/key_manager/key_manager.py`

Le Key Manager utilise :
- **Keyring OS** (GNOME Keyring, macOS Keychain, Windows Credential Manager)
- Fallback Fernet pour headless (dérivé du `machine-id`)
- `locked` persistant — `get_key()` lève `KeyLockedError`
- `key_display` dérivé en mémoire (jamais stocké)

**État** : ✅ Correct. Aucune clé API ne doit jamais être stockée en clair sur le disque.

#### 2.4.2 Variables d'environnement dans `.env` (RISK : LOW)
**Fichiers** : `.env`, `.env.template`, `.env.committed`

Les fichiers `.env` contiennent des clés API en clair. Bien que `.gitignore` devrait exclure `.env`, il faut vérifier.

**Recommandation** : Vérifier que `.env` est dans `.gitignore` et jamais commité.

### 2.5 Rate limiting

#### 2.5.1 Rate limiter — bonne couverture mais limites (RISK : LOW)
**Fichier** : `services/ratelimit.py`

Le rate limiter couvre :
- `keys/` : 10/min
- `tools/` : 10/min
- `agents/spawn|delete` : 20/min
- `agents/` (autres) : 120/min
- `llm/chat` : 30/min
- Défaut : 30/min
- Token/min : 50,000
- Token/day : 1,000,000

**Recommandation** :
- Ajouter un rate limit plus strict pour `llm/chat/stream` (le streaming peut générer beaucoup de tokens).
- Considérer un rate limit global par IP (pas seulement par route).

### 2.6 Sandboxing des agents

#### 2.6.1 FsAuthManager — allowlist par agent (RISK : LOW)
**Fichier** : `services/fs_auth.py`

Chaque agent a une allowlist de chemins hôte (mode `r` ou `rw`). Vérifié à **chaque appel** de skill host_*.

**État** : ✅ Correct. Aucun accès hôte par défaut tant qu'un grant n'est pas donné.

#### 2.6.2 Agent storage quota (RISK : LOW)
**Fichier** : `AgentFrameWork/agent_storage.py`

Quota soft de 10 Mo par agent (configurable jusqu'à 10 Go pour Docker). Demande d'augmentation escaladée au gestionnaire de ressources.

**État** : ✅ Correct.

---

## 3. Vérification des contrats et spécifications

### 3.1 Hard-check des contrats (`hardcheck/verify.py`)

**Résultat** : ❌ **2 erreurs sur 185 vérifications**

#### 3.1.1 Erreur 1 : Import non déclaré dans `services/agent_manager`
```
services/agent_manager: import projet NON déclaré: ['services.api.afd_client']
```

Le service `agent_manager` importe `services.api.afd_client` mais ne le déclare pas dans son `_contract/dependencies.py`.

**Fichier concerné** : `services/agent_manager/_contract/dependencies.py`

**Recommandation** : Ajouter `'services.api.afd_client': ['AFDSocketClient']` à `CONSUMES`.

#### 3.1.2 Erreur 2 : Routes servies mais non déclarées dans `services/api`
```
services/api: routes servies mais NON déclarées: ['catalogue/agents/delete', 'catalogue/agents/get', 
'catalogue/agents/inline', 'catalogue/agents/list', 'catalogue/agents/save', 'catalogue/all', 
'catalogue/behaviors/delete', 'catalogue/behaviors/get', 'catalogue/behaviors/list', 
'catalogue/behaviors/save', 'catalogue/personalities/delete', 'catalogue/personalities/get', 
'catalogue/personalities/list', 'catalogue/personalities/save', 'catalogue/roles/delete', 
'catalogue/roles/get', 'catalogue/roles/list', 'catalogue/roles/save', 'catalogue/skills/delete', 
'catalogue/skills/get', 'catalogue/skills/list', 'catalogue/skills/save', 'lib/list', 
'lib/resolve', 'lib/scan', 'version']
```

Le daemon (`services/api/daemon.py`) sert **26 routes** qui ne sont pas déclarées dans le contrat `services/api/_contract/interface.py`. Ces routes proviennent du nouveau module `catalogue_api.py` (V0.7.1) et `catalogue_agents.py` (V0.7.1) qui n'ont pas été ajoutées au contrat.

**Fichier concerné** : `services/api/_contract/interface.py`

**Recommandation** : Ajouter les 26 routes manquantes à `EXPOSES` dans le contrat. Exemple :
```python
"catalogue/skills/list": [],
"catalogue/skills/get": ["name"],
"catalogue/skills/save": ["name", "data"],
"catalogue/skills/delete": ["name"],
# ... etc pour behaviors, personalities, roles, agents, lib/*
"version": [],
```

### 3.2 Spécification AGENT_SPEC.md vs implémentation

#### 3.2.1 Tables BDD — conformité ✅
Le schéma BDD (`modules/sql/agents_schema.sql`) implémente correctement :
- Table `agents` avec `occupation` (CHECK), `status` (CHECK), `config_json`, `resources_json`, `variables_json`, `state_json`, `successor_id`.
- Table `agent_runtime` avec `thread_id`, `pid`, `heartbeat_at`, `started_at`, `current_step`.

#### 3.2.2 Interface `Agent` / `AgentManager` — conformité partielle ⚠️
**Spécification** (`AGENT_SPEC.md:102-123`) :
- `Agent.hydrate(agent_id, db)` ✅
- `Agent.execute(request)` ✅
- `Agent.dehydrate()` ✅
- `Agent.get_status()` ✅
- `Agent.to_dict()` ✅
- `AgentManager.list_active()` ✅
- `AgentManager.check_heartbeats(max_age=30)` ✅
- `AgentManager.kill(agent_id)` ✅

**Note** : L'interface est implémentée mais évoluée au-delà de la spécification V0.6.5 (FSM, signaux, spawn, handoff, storage, budget).

#### 3.2.3 Occupations — conformité ✅
- `continue` : agents persistants (architecte, agent-manager) ✅
- `noncontinue` : agents temporaires (codeur, critique) ✅
- `disparate` : agents rares (expert légal, auditeur) ✅

### 3.3 Spécification workflow_yaml_spec.md vs implémentation

#### 3.3.1 Types de steps — conformité ✅
Tous les types déclarés sont implémentés dans `fsm_interpreter.py` :
- `llm_call` → `_step_llm_call` ✅
- `call` → `_step_call` ✅
- `tool_call` → `_step_tool_call` (déprécié) ✅
- `switch` → `_step_switch` ✅
- `set_variable` → `_step_set_variable` ✅
- `for`/`while` → `_step_for`/`_step_while` ✅
- `break`/`continue` → gérés dans les boucles ✅
- `sleep` → `_step_sleep` ✅
- `spawn` → `_step_spawn` ✅
- `handoff` → `_step_handoff` ✅
- `agent_call` → `_step_agent_call` ✅
- `end` → `_step_end` ✅

#### 3.3.2 Entrypoints — conformité ✅
Le format `entrypoints.<name>.steps` est implémenté avec rétrocompatibilité `workflow.steps`. Le sélecteur par onglets est présent dans GraphView.

### 3.4 Spécification ARCHITECTURE_API.md vs implémentation

#### 3.4.1 Transport — conformité partielle ⚠️
- ✅ HTTP/JSON sur 127.0.0.1
- ✅ Token de session (`~/.modelweaver/api.token`)
- ❌ Versionné `/v1/` — le daemon n'utilise pas de préfixe de version dans les routes
- ❌ Socket Unix — non implémenté (documenté comme alternative)

**Recommandation** : Ajouter le préfixe `/v1/` aux routes ou documenter l'absence de versionning.

#### 3.4.2 Sécurité — conformité partielle ⚠️
- ✅ Bind sur 127.0.0.1 (daemon principal)
- ❌ `catalogue_server.py` bind sur 0.0.0.0 (voir §2.2.1)
- ✅ Token de session
- ⚠️ Opérations sensibles journalisées (audit.py) mais pas toutes

#### 3.4.3 Routes — conformité partielle ⚠️
Le contrat `EXPOSES` déclare 76 routes, mais 26 sont manquantes (voir §3.1.2).

### 3.5 Spécification ARCHITECTURE_MODULES.md vs implémentation

#### 3.5.1 Convention `_contract/` — conformité ✅
- ✅ Tous les modules/services ont `_contract/interface.py` et `_contract/dependencies.py`
- ✅ `hardcheck/verify.py` valide exports, dépendances, frontières AST
- ✅ 17 unités, 185 vérifications (183 PASS, 2 FAIL)

#### 3.5.2 Migration des services — conformité partielle ⚠️
**Documenté** (`VERSIONS.md:101-106`) :
- ✅ Superviseur Rust repointé vers `services/*/service.py`
- ⚠️ Bundling Tauri — à vérifier si `modules/` + `services/` sont embarqués
- ⚠️ `gui_helper.py` — décomposition en cours, wrappers encore présents

---

## 4. Points forts

### 4.1 Architecture modulaire solide
- Séparation claire modules (logique) / services (processus)
- Contrats `_contract/` avec vérification `hardcheck`
- Daemon API découplé de la GUI

### 4.2 Sécurité des clés API
- Keyring OS, fallback Fernet
- Verrouillage manuel par clé
- `key_display` jamais stocké

### 4.3 Agent Framework complet
- FSM interpreter avec boucles, conditions, erreurs
- StreamBus (mémoire + SQLite WAL cross-process)
- Signaux parallèles (pause/resume/kill/configure)
- Skills system (63+ skills)
- Collaboration multi-agents (git distribué, inbox, chatroom)
- Rate limiting, sandboxing, quota disque, budget tracking

### 4.4 Qualité des tests
- E2E agent framework : 51/51 PASS
- E2E mini-entreprise : 31/31 PASS
- Tests de conflits git, échecs skills, etc.

### 4.5 Frontend modulaire
- GUI découpée en panneaux (`panels/*.tsx`)
- CatalogTree réutilisable (recherche floue, foldable)
- GraphView FSM (react-flow + dagre)
- CodeEditor maison (pas Monaco)

---

## 5. Points faibles et améliorations

### 5.1 Contrats non à jour (PRIORITÉ HAUTE)
**Problème** : 26 routes du daemon non déclarées dans le contrat `EXPOSES`.

**Impact** : Le hard-check échoue, le SDK TS ne peut pas être généré correctement, les clients ne savent pas quelles routes sont disponibles.

**Solution** : Mettre à jour `services/api/_contract/interface.py` avec les 26 routes manquantes.

### 5.2 Import non déclaré (PRIORITÉ HAUTE)
**Problème** : `services/agent_manager` importe `services.api.afd_client` sans le déclarer.

**Solution** : Ajouter à `CONSUMES` dans `services/agent_manager/_contract/dependencies.py`.

### 5.3 `exec()` sur code inline (PRIORITÉ HAUTE — SÉCURITÉ)
**Problème** : `exec(inline_code, ns)` dans `skill_manager.py` permet l'exécution arbitraire de code Python.

**Solution** :
- Supprimer `implementation.code` ou utiliser `RestrictedPython`.
- Auditer les YAML de skills pour détecter des `implementation.code`.

### 5.4 `shell=True` dans recipe_parser (PRIORITÉ HAUTE — SÉCURITÉ)
**Problème** : Injection de commandes possible via `shell=True`.

**Solution** : Utiliser `shell=False` avec liste d'arguments.

### 5.5 `catalogue_server.py` bind 0.0.0.0 (PRIORITÉ HAUTE — SÉCURITÉ)
**Problème** : Serveur catalogue exposé sur toutes les interfaces.

**Solution** : Changer en `127.0.0.1` ou déprécier le serveur.

### 5.6 Versionning API (PRIORITÉ MOYENNE)
**Problème** : Le daemon n'utilise pas de préfixe `/v1/` dans les routes, contrairement à la spécification.

**Solution** : Ajouter `/v1/` aux routes ou mettre à jour la documentation.

### 5.7 `preexec_fn` déprécié (PRIORITÉ MOYENNE)
**Fichier** : `services/sandbox.py:55`

**Problème** : `preexec_fn` est déprécié dans Python 3.11+ au profit de `process_group`.

**Solution** : Migrer vers `process_group=0` (ou `start_new_session=True` qui est déjà utilisé).

### 5.8 DEBUG=true dans CodeEditor (PRIORITÉ MOYENNE)
**Fichier** : `interfaces/main/GUI/official/gui/src/components/CodeEditor.tsx:36`

**Problème** : `const DEBUG = false;` — le commentaire dit "TODO retirer avant release". Actuellement `false`, mais le code de debug est présent.

**Solution** : Supprimer le code de debug si non nécessaire, ou le garder mais s'assurer qu'il est `false` en production.

### 5.9 GUI non rebuild (PRIORITÉ MOYENNE)
**Problème** : Selon `VERSIONS.md:367`, le binaire GUI Tauri n'a pas été rebuild pour V0.7.4 — les onglets chat/agents sont absents du binaire releasé.

**Solution** : Rebuild du binaire Tauri avec les dernières modifications.

### 5.10 Documentation `programming_rules.md` obsolète (PRIORITÉ BASSE)
**Fichier** : `docs/programming_rules.md`

**Problème** : Documente V0.2, mentionne `modelweaver.sh` et `modelweaver.py` (V0.1) comme interdits de modifier. Ne reflète pas l'architecture actuelle (modules/services, daemon, AFD, etc.).

**Solution** : Mettre à jour ou remplacer par une documentation actuelle.

### 5.11 Tsconfig error pré-existant (PRIORITÉ BASSE)
**Problème** : `VERSIONS.md:32` mentionne une erreur tsconfig (`module` vs `moduleResolution`), mais `vite build` fonctionne.

**Solution** : Corriger le tsconfig pour éviter les warnings.

### 5.12 Pas de versionning de l'API (PRIORITÉ BASSE)
**Problème** : Contrairement à `ARCHITECTURE_API.md`, le daemon n'utilise pas `/v1/` dans les routes.

**Solution** : Ajouter le préfixe ou documenter l'absence de versionning.

---

## 6. Tests

### 6.1 Tests existants
| Test | Type | Résultat |
|------|------|----------|
| `tests/e2e_agent_framework.py` | E2E | 51/51 PASS |
| `tests/e2e_mini_entreprise.py` | E2E | 31/31 PASS |
| `tests/test_fsm_skill_failure.py` | Unit | 5/5 PASS |
| `tests/test_git_conflict_resolution.py` | Unit | 8/8 PASS |
| `tests/test_live_multiagent.py` | Live | 1/1 PASS |
| `hardcheck/verify.py` | Contract | 183/185 PASS (2 FAIL) |

### 6.2 Tests manquants
- **Tests de sécurité** : Aucun test dédié aux vulnérabilités (exec, shell=True, binding).
- **Tests de rate limiting** : Aucun test unitaire du rate limiter.
- **Tests de sandboxing** : Aucun test du `Sandbox` class.
- **Tests de FsAuthManager** : Aucun test unitaire.

---

## 7. Recommandations prioritaires

### 7.1 Immédiates (blockantes)
1. **Corriger les 2 erreurs de hard-check** (routes manquantes + import non déclaré)
2. **Corriger `shell=True`** dans `recipe_parser.py`
3. **Corriger le binding `0.0.0.0`** dans `catalogue_server.py`

### 7.2 Courtes (prochaine release)
4. **Auditer/sécuriser `exec(inline_code)`** dans `skill_manager.py`
5. **Rebuild du binaire Tauri** pour V0.7.4
6. **Corriger le tsconfig** (warning pré-existant)

### 7.3 Moyennes terme
7. **Ajouter des tests de sécurité** (exec, shell, binding, rate limit)
8. **Mettre à jour `programming_rules.md`** ou le remplacer
9. **Considérer le versionning API** (`/v1/`)
10. **Migrer `preexec_fn`** vers `process_group`

### 7.4 Long terme
11. **Déprécier `catalogue_server.py`** au profit du daemon
12. **Générer le SDK TS** depuis les contrats `EXPOSES`
13. **Brancher `hardcheck` en pre-commit + CI**
14. **Auditer les recettes d'installation** pour injection de commandes

---

## 8. Conclusion

ModelWeaver est un projet **très bien architecturé** avec une séparation claire des responsabilités, un système de contrats solide, et une couverture de tests E2E excellente. Les points forts sont l'Agent Framework complet, la sécurité des clés API, et l'architecture modulaire.

Les **problèmes critiques** à corriger avant release sont :
1. Les 2 erreurs de hard-check (contrats non à jour)
2. Le `shell=True` dans `recipe_parser.py` (injection de commandes)
3. Le binding `0.0.0.0` dans `catalogue_server.py` (exposition réseau)
4. Le `exec(inline_code)` dans `skill_manager.py` (exécution arbitraire de code)

Une fois ces problèmes corrigés, le projet est en bonne santé pour une release stable.
