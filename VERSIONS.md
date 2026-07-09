# Versions — Périmètres et Limites

## V0.1 (Terminée ✅)

### Périmètre global

Tout le socle : script d'installation complet, vérification de toutes les dépendances, composants (Ollama, LiteLLM, OpenCode, Open WebUI), gestionnaire de paquets Python au choix, linkage, configuration API, fallback, téléchargement tinyllama, interface (CLI + GUI légère), proxy context-aware avec gitingest, pipeline de mise à jour automatique des modèles.

### Décisions architecturales

| Décision | Choix | Raison |
|----------|-------|--------|
| Routeur LLM | **LiteLLM Router** (proxy custom `litellm_router_proxy.py`) | SmarterRouter trop complexe, ne gérait pas correctement le multi-providers natif. LiteLLM Router + fallback séquentiel plus simple et plus fiable. |
| Contexte projet | **gitingest** (intégré au proxy) | Injection auto de l'arborescence et des fichiers clés. Remplace le SmarterRouter Benchmark + provider.db |
| Priorité modèle | **Séquentiel avec budgets** | Essai en ordre de priorité, fallback si budget dépassé ou erreur. Budgets configurables par modèle. |
| Signature réponse | `[Répondu par : X]` | Ajoutée au contenu par le proxy. Permet de savoir quel modèle a répondu après un fallback. |
| Visibilité fallback | Champs `_responded`, `_eliminated`, `_errors` | Retournés dans la réponse API pour traçabilité complète. |
| Streaming | Supporté | Le proxy stream la réponse + signature en un seul flux. |
| Cache contexte | 60s par défaut | Évite de re-générer le contexte gitingest à chaque requête. |
| Fallback personnalisable | `fallback_preferences.yaml` | Fichier séparé éditable définissant l'ordre des groupes, patterns et cooldowns. |
| Pipeline mise à jour | `maj-liste-litellm.py` | Fetch models.dev → filtre par clé → merge → possibly-dead → ordonner-fallback. |
| Clés API | `.env` → `prepare_keys.py` | Source de vérité unique, mappée automatiquement aux providers litellm. |

### Sous-versions

**V0.1.0** — Structure de base
- [x] `modelweaver.sh` : bootstrap minimal (sh portable) + `modelweaver.py` (cœur Python)
- [x] Tests Docker : `test.sh`, `clean.sh`, `docker-backup/`
- [x] License, `VERSIONS.md`, `.gitignore`, git
- [x] `.modelweaver/cache/` — répertoire de cache pour les téléchargements
- [x] `download_with_cache()` dans modelweaver.py (évite re-téléchargements, checksums SHA256)
- [x] **V0.1.0.1** — Vérification de toutes les dépendances système
- [x] **V0.1.0.2** — Gestionnaire de paquets Python (choix pip/uv/rye)
- [x] **V0.1.0.3** — Docker de base pour système ancien

**V0.1.1** — Installation des composants
- [x] **V0.1.1.1** — Ollama (binaire, optionnel, désactivé si RAM < 8 Go)
- [x] **V0.1.1.2** — LiteLLM (python-module)
- [x] **V0.1.1.3** — OpenCode (binaire)
- [x] **V0.1.1.4** — Open WebUI (python-module, optionnel)

**V0.1.2** — Linkage
- [x] Injection auto des paramètres de connexion entre les briques
- [x] Vérification que les composants communiquent
- [x] Tests Docker + snapshot

**V0.1.3** — Configuration API
- [x] Guide pas-à-pas (`.env.template` avec URLs docs)
- [x] Stockage sécurisé (`.env`)
- [x] Gestion des providers cloud (12 fournisseurs détectables)
- [x] Tests Docker + snapshot

**V0.1.4** — Gestion du Fallback
- [x] Détection d'erreur 429/surcharge (`detect_overload`)
- [x] Basculement automatique (`router_settings` + chaînes de repli)
- [x] Logs des basculements (`fallback.log`)
- [x] Validation des fournisseurs
- [x] Notes : Le fallback LiteLLM/ModelWeaver a été remplacé par notre proxy custom `litellm_router_proxy.py` qui essaie les modèles en ordre de priorité avec budgets contexte
- [x] Tests Docker + snapshot

**V0.1.5** — Téléchargement tinyllama
- [x] Installation automatique du modèle de test (637 Mo)
- [x] Vérification du fonctionnement local (prompt + réponse)
- [x] Tests Docker + snapshot

**V0.1.6** — Interface
- [x] CLI enrichie (commandes : install, check, config, status, menu, help)
- [x] Flags : --mode, --only, --skip-audit, --skip-tinyllama
- [x] Base GUI légère (menu interactif terminal)
- [x] Tests Docker + snapshot

**V0.1.7** — Bridge OpenCode explicite et tracé de route
- [x] La commande `opencode` reste directe et n'est plus remplacée par le wrapper.
- [x] Le bridge ModelWeaver est installé sous `opencode-modelweaver`.
- [x] Fallback visible et logs de route dans `.modelweaver/route_trace.log`.
- [x] Gestion explicite des erreurs d'authentification et de saturation comme déclencheurs de repli.
- [x] Tests de régression ajoutés pour le wrapper et le plan de routage.

**V0.1.8** — Routage intelligent et découverte dynamique des modèles
- [x] Découverte dynamique via `opencode models` (détection de 358+ modèles)
- [x] Deux ordres de routage : `--routing test` (Groq → OpenRouter → Ollama) et `--routing main` (OpenCode Zen deepseek → ...)
- [x] `model_scores.json` avec cycle de vie complet (check, responded, disparu, backoff exponentiel ×1.5)
- [x] Script de connectivité parallélisé (`test_model_connectivity.py` : 8 threads, timeout 120s)
- [x] Propriété `free_key` sur les providers : protection contre la facturation accidentelle
- [x] Nettoyage automatique des modèles disparus depuis > 30 jours

**V0.1.9** — Test et debug sur machine personnelle
- [x] Déploiement sur le home de l'utilisateur
- [x] Tests réels (hors Docker) de l'installation complète
- [x] Debug des problèmes spécifiques à l'environnement local
- [x] Validation des performances et de la fiabilité
- [x] **Décision** : SmarterRouter archivé (`SmarterRouter_ARCHIVED/`) car trop complexe et mal adapté au multi-providers
- [x] **Décision** : Proxy custom `litellm_router_proxy.py` basé sur LiteLLM Router avec fallback séquentiel
- [x] **Décision** : gitingest pour contexte projet auto-injecté (remplace provider.db + benchmark)
- [x] **Décision** : Budgets contexte par modèle (Groq=100K, Gemini=1M...)
- [x] **Décision** : Signature `[Répondu par : X]` dans la réponse
- [x] `opencodesmart` wrapper mis à jour (proxy + opencode)
- [x] SmarterRouter archivé, plus rien n'en dépend

**V0.1.10** — Pipeline de mise à jour automatique
- [x] **V0.1.10.1** — Intégration gitingest
  - [x] `pip install gitingest` (système + venv)
  - [x] Ajout de `context` (gitingest) dans `manifest.json`
  - [x] Installation automatisée via `modelweaver.py`
- [x] **V0.1.10.2** — Déploiement du proxy context-aware
  - [x] Copie de `litellm_router_proxy.py` dans `.modelweaver/`
  - [x] Service systemd user `modelweaver-proxy.service` (autostart, restart on failure)
  - [x] Budgets et context_settings déjà dans la config YAML
- [x] **V0.1.10.3** — Auto-génération `opencode.json`
  - [x] Config globale `~/.config/opencode/opencode.json` → proxy 8000
  - [x] Config projet `ModelWeaver/opencode.json` → proxy 8000
  - [x] Modèle par défaut : `opencode-engine`
- [x] **V0.1.10.4** — Configuration budgets contexte
  - [x] `context_settings` avec `project_root`, `max_context_chars`, `refresh_interval`, `exclude_patterns`
  - [x] `model_budgets` par groupe (Groq=400K, Gemini=4M chars...)
  - [x] Cache 60s du contexte gitingest
- [x] **V0.1.10.5** — Pipeline maj-liste-litellm
  - [x] `prepare_keys.py` : lit `.env` → `.modelweaver/keys.json`
  - [x] `maj-liste-litellm.py` : fetch models.dev → filtre par clé → merge config → possibly-dead → ordonner-fallback
  - [x] `ordonner-fallback.py` : lit `fallback_preferences.yaml` → applique priorités
  - [x] `.modelweaver/fallback_preferences.yaml` : fichier séparé éditable
  - [x] 521 modèles, 7 possibly-dead, Google → Mistral → Zen → Nemotron Free → Free → Other

---

## V0.2 (Terminée ✅) — Le Grand Split en Modules

**Objectif** : Refonte architecturale complète pour passer d'un script monolithique à un système modulaire de 9 composants interconnectés.

### Architecture (3 Couches, 9 Modules)

**Couche 1 : Données, Sécurité & Découverte Automatique**
1.  **Le Catalogue** (`/catalogue`) : Source de vérité (JSON). Répertorie fournisseurs, modèles et outils.
2.  **Le Gestionnaire de Clés** (`/key_manager`) : Coffre-fort et onboarding automatique (détection de signature, auto-enrichissement via `/models` et métadonnées IA).

**Couche 2 : Le Moteur Logique (Le Core)**
3.  **Le Checkeur** (`/checker`) : Inspection du système (PATH, Ollama, etc.) $\to$ `system_state.json`.
4.  **L'Installeur** (`/installer`) : Préparation d'environnement (apt, winget, brew, sandbox locale).
5.  **Le Gestionnaire de Conteneurs** (`/container_manager`) : Orchestration Docker pour exécution isolée (sandbox).
6.  **Le Module de Test** (`/test_runner`) : Validation des scripts agents dans le conteneur Docker.
7.  **Le Plombier** (`/plumber`) : Routeur d'API intelligent (fallback transparent, gestion des quotas, adaptateurs OpenAI/Gemini).

**Couche 3 : L'Interface Utilisateur (UI)**
8.  **L'Organiseur** (`/organiser`) : Interface de configuration (CLI/TUI) basée sur `system_state.json`.
9.  **Le Dashboard** (`/dashboard`) : Tour de contrôle (Play/Stop, logs temps réel, monitoring ressources).

### Stratégie de développement (9 étapes)

Chaque étape consiste à développer un module et à le valider par des tests.

1.  [x] **Étape 1** : Module `/catalogue`
    * [x] 1.1. Définition des schémas JSON (Fournisseurs, Modèles, Outils).
    * [x] 1.2. Implémentation de la lecture/écriture du Catalogue.
    * [x] 1.3. Intégration de la récupération automatique via `models.dev` et l'API NVIDIA.
    * [x] 1.4. Test unitaire du module Catalogue.
2.  [x] **Étape 2** : Module `/key_manager`
    * [x] 2.1. Implement the vault (storage of keys).
    * [x] 2.2. Implement automatic onboarding.
3.  [x] **Étape 3** : Module `/checker`
    * [x] 3.1. Inspection du système (PATH, Ollama, etc.) $\to$ `system_state.json`.
4.  [x] **Étape 4** : Module `/installer`
    * [x] 4.1. Préparation d'environnement (apt, winget, brew, sandbox locale).
5.  [x] **Étape 5** : Module `/container_manager`
    * [x] 5.1. Orchestration Docker pour exécution isolée (sandbox).
6.  [x] **Étape 6** : Module `/test_runner`
    * [x] 6.1. Validation des scripts agents dans le conteneur Docker.
7.  [x] **Étape 7** : Module `/plumber`
    * [x] 7.1. Routeur d'API intelligent (fallback transparent, gestion des quotas, adaptateurs OpenAI/Gemini).
8.  [x] **Étape 8** : Module `/organiser`
    * [x] 8.1. Studio de création visuel (Low-Code, drag-and-drop) basé sur `system_state.json`.
9.  [x] **Étape 9** : Module `/dashboard`
    * [x] 9.1. Tour de contrôle (Play/Stop, logs temps réel, monitoring ressources).


### Limites & Focus
- **Focus initial** : Développement exclusif sur Ubuntu/Linux.
- **Conception** : Isolation stricte des commandes système et utilisation systématique de `pathlib`.
- **Workflow** : Clé $\to$ Découverte $\to$ Catalogue $\to$ UI $\to$ Agents $\to$ Plombier $\to$ Sandbox.

---

## V0.3 (Terminée ✅) — Intégration SQLite Complète

**Objectif** : Remplacer les JSON bordéliques par une structure SQLite relationnelle. Le catalogue devient la BDD globale.

### Architecture BDD

**Deux bases SQLite distinctes :**
1. **Catalogue DB** (`.modelweaver/catalogue.db`) — référence publique, mise à jour possible via BDD distante
2. **Local DB** (`.modelweaver/modelweaver.db`) — état local (clés, outils installés, config)

### Tables prévues

| Table | Rôle | Champs clés |
|-------|------|-------------|
| `providers` | Fournisseurs d'API | id unique, nom, type, limites, prochain reset |
| `api_keys` | Clés API | fournisseur_id (FK), tag free/payant, date péremption |
| `models` | Modèles connus (indépendants) | nom, contexte, capacités, métadonnées |
| `provider_models` | Liaison fournisseur↔modèles | nom chez le fournisseur, limites, rate limits, reset |
| `tools` | Outils du catalogue | vérification existence, liste fournisseurs |
| `local_tools` | Outils installés localement | version, chemin, statut |
| `commands` | Commandes supportées implémentées | utilitaires non triviaux pour la gestion IA |

### Tables catalogue
- `catalogue_providers`, `catalogue_tools` — allégées, juste de quoi vérifier l'existence et lister les fournisseurs

### Sous-versions

**V0.3.0** — Design du schéma et migration
- [x] Définition complète du schéma SQLite (tables, indexes, contraintes)
- [x] Script de migration des JSON → SQLite
- [x] Tests de validation du schéma

**V0.3.1** — Module catalogue porté sur SQLite
- [x] Remplacement des JSON par des queries SQLite
- [x] Sync catalogue distant → local

**V0.3.2** — Module key_manager porté sur SQLite
- [ ] Stockage des clés en base avec chiffrement (repoussé)
- [x] Tags free/payant, dates péremption

**V0.3.3** — Module checker/installer porté sur SQLite
- [x] État système dans SQLite
- [x] Traçabilité des installations

**V0.3.4** — Module plumber porté sur SQLite
- [x] Constructeurs des repositories sans arguments utilisés
- [ ] Limites, rate limits, quotas lus depuis la BDD (repoussé V0.5)
- [ ] Routage basé sur les données BDD (repoussé V0.5)

**V0.3.5** — Catalogue distant + synchro HTTP
- [x] `sql/catalogue_server.py` : serveur HTTP sur port configurable
- [x] `CatalogueDB.sync_from_url()` : sync toutes les tables depuis une URL
- [x] `CatalogueDB._ensure_schema()` : auto-création des tables si vide
- [x] `install_in_docker.py` : version SQLite (utilise `ModelWeaverDB` + `CatalogueDB`)
- [x] `build-docker.sh --sqlite` : copie `catalogue.db` → `catalogue.remote.db`, démarre le serveur, injecte `CATALOGUE_URL` dans le container

**V0.3.6** — Installer DB-driven + enrichissement catalogue
- [x] `Installer.install(tool_dict)` : dispatch par `install_method` (pip, apt, brew, winget, direct-url, github-release, installer-script, package-manager)
- [x] `Installer.uninstall()` : stub vide
- [x] Cache : `_cached_download()` évite re-téléchargements
- [x] `scan_installed()` préserve `install_method` et `tool_type` existants
- [x] `ToolRepository.save()` met à jour seulement les champs fournis
- [x] `manifest.json` version 2.0 : descriptions, `install_method`, `default_download_url`, `allowed_platforms`, `allowed_arches`
- [x] Migration enrichie : copie les nouveaux champs vers `tools` + `catalogue_tools`
- [x] Test Docker --sqlite validé : installation `curl`, `git`, `python3` via l'Installer

---

## V0.4 (En cours 🚧) — Agent Factory & Orchestration

**Objectif** : Factory d'agents spécialisés, orchestration multi-agents, exécution planifiée.

### Architecture

**Paradigme Phénix (Stateless Code / Stateful DB)** : les agents n'existent que comme lignes en BDD. Un Ticker asynchrone les hydrate à la demande, exécute la tâche LLM, puis les déshydrate. 0% CPU au repos.

**Couplage Agent + Rôle + Modèle** : chaque agent est lié déterministiquement à un modèle/provider et à un rôle (fichier YAML). Les petits modèles ne peuvent pas s'auto-attribuer des tâches critiques.

### Composants livrés (V0.4.0)

| Composant | Fichier | Rôle |
|-----------|---------|------|
| Tables BDD | `sql/modelweaver_schema.sql` | 5 tables : model_providers, agents, sessions, agent_messages, wakeup_calls |
| Repositories | `sql/agent_repository.py` | 5 DAOs (ModelProvider, Agent, Session, AgentMessage, WakeupCall) |
| Agent Factory | `agents/factory.py` | `createAgent()`, `Agent.execute()`, `Agent.exit()`, `create_request_agent()` |
| Worker | `agents/worker.py` | Hydrate → appel HTTP OpenAI direct → déshydrate |
| Ticker | `agents/ticker.py` | Boucle asynchrone, anti-fantôme, wakeup event |
| Rôles | `agents/role_manager.py` | Chargement YAML depuis `agents/roles/` |
| Role codeur | `agents/roles/codeur.yaml` | System prompt + skills + config |

### Sous-versions

**V0.4.0** — Agent Factory ✅
- [x] Architecture Phénix : agents = lignes en BDD, stateless, 0 CPU au repos
- [x] 5 tables SQLite avec WAL, indexes, contraintes
- [x] Repositories DAO (même pattern que sql/db.py)
- [x] `ModelWeaverDB._ensure_schema` auto-applique tout le schema (IF NOT EXISTS)
- [x] `AgentFactory.createAgent(name, role_type, provider_id, config)` + injecte le system_prompt du rôle
- [x] `Agent.execute(request, additional_context, reset_context, session_id, skill)`
- [x] `Agent.exit()` marque STOPPED + archive les sessions
- [x] `AgentFactory.create_request_agent()` jetable (one-shot)
- [x] Worker : appel HTTP direct en format OpenAI compatible (plus de callback)
- [x] Chargement des clés API depuis `api_keys` via `model_providers.api_key_ref`
- [x] Worker gère les erreurs HTTP (timeout, 429, 500) proprement
- [x] Claim atomique via `UPDATE ... WHERE status='TODO'` (pas de BEGIN IMMEDIATE)
- [x] Anti-fantôme : `reset_busy()` au démarrage du Ticker
- [x] Catch-up : les tâches en retard sont traitées séquentiellement
- [x] `AsyncTicker` : asyncio.Event pour réveil immédiat, polling configurable
- [x] `python -m agents [--once] [--list-roles]` — CLI du Ticker
- [x] Rôle `assistant` : prompt généraliste, température 0.7
- [x] Rôle `codeur` : prompt code, température 0.3, skills code_gen/review/debug/refactor
- [x] `RoleManager` : charge/sauvegarde les YAML dans `agents/roles/`
- [x] Test unitaire : 7 tests (schéma, création, execute, wakeup lifecycle, anti-ghost, rôles, one-shot)
- [x] Test intégration Agent Codeur : génération d'un tic-tac-toe fonctionnel via Groq (API réelle)
- [x] Test Docker : conteneur isolé, agent codeur → script valide, vérifications passées

**V0.4.1** — Workflow DSL & Connexions ✅
- [x] Spec YAML complète (`agents/role_pipeline_spec.yaml`) : workflow, llm_call, switch, sleep, end, signal_successor, save_state, connect, branches
- [x] 11 rôles classiques créés (assistant, codeur, architecte, controleur_qualite, relecteur, debugger, planificateur, chercheur, documentaliste, orchestrateur, critique)
- [x] Classification par class/sub_class dans chaque rôle
- [x] Tables orchestration : agent_queue, chatroom_messages, shared_tasks, watchers
- [x] Repositories orchestration : AgentQueue, Chatroom, SharedTask, Watcher
- [x] BDD : agents.state_json, agents.successor_id, status TERMINATED, table agent_connections
- [x] PipelineExecutor base (concat, extract_context, if, loop, set_variable, translate_context, call_function)
- [x] ConnectionRepository + hook dans ModelWeaverDB (`sql/orchestration_repository.py`, `db.py:OrchestrationDBMixin`)
- [x] AgentRepository mis à jour (state_json, successor_id, TERMINATED) — `save_state()`, `load_state()`, `set_successor()`, `terminate()`
- [x] DSL Executor complet (switch, sleep, llm_call, end, output_capture) — `agents/dsl_executor.py`
- [x] AgentFactory/Agent : `exit(successor_role, successor_config)`, `save_state()`/`restore_state()`
- [x] Mécanisme de succession : `signal_relay()` + WatcherService écoute + Worker `signal_successor_fn`
- [x] Worker exécute le workflow DSL (`worker.py` : si pipeline → `_execute_workflow()`, sinon → `_call_llm_simple()`)
- [x] Branches : connect/disconnect automatiques selon config rôle (chatroom, todo, queue)
- [ ] Tests unitaires complets du nouveau système
- [ ] Test intégration : boucle réflexive (génération + critique + correction) via Groq
- [ ] Test Docker : workflow multi-agents en conteneur
- [ ] **Commit V0.4.1**

**V0.4.2** — Orchestration multi-agents ✅
- [x] Orchestrateur fonctionnel : répartition des tâches via `Dispatcher.find_compatible_agent()`, provisionnement
- [x] Watchers opérationnels : `WatcherService.tick()`, `_check_condition()`, `_trigger_agent()`
- [x] Communication inter-agents : `AgentQueueRepository` + `ChatroomRepository` + `SharedTaskRepository` fonctionnels
- [x] `ProvisioningService` : création dynamique d'agents
- [x] `ReviewService` : validation récursive
- [x] `AutoDebugService` : boucle Codeur → TestRunner → Debugger
- [x] `WatchdogService` : détection des agents zombies
- [ ] Test intégration : 3+ agents collaborant sur une tâche réelle
- [ ] Test Docker : orchestration multi-agents en conteneur
- [ ] **Commit V0.4.2**

**V0.4.3** — Planification et automatisation ✅
- [x] Tâches planifiées (cron-like) : `Scheduler` + `ScheduledJobRepository` + table `scheduled_jobs`
- [x] Pipelines de traitement configurables : `PipelineExecutor` + `DSLExecutor`
- [ ] Test intégration : pipeline planifié récurrent
- [ ] **Commit V0.4.3**

**V0.4.x** — Boucle spec → code → test → debug (récursif)
- [ ] LLM teste les agents en profondeur, identifie les bugs, les corrige
- [ ] Migration BDD propre entre sous-versions (ALTER TABLE si nécessaire)
- [ ] Robustesse : gestion des timeouts, erreurs réseau, concurrence
- [ ] Chaque sous-point commit séparé avec vérification dédiée
- [ ] **Commit après chaque sous-point**

---

## V0.5 (En cours 🚧) — GUI Installateur

**Objectif** : Première interface graphique utilisable — installateur de ModelWeaver.

### ⚠️ Blocage architectural (à résoudre avant fin V0.5)
Les colonnes JSON dans `tools` (`allowed_platforms`, `allowed_arches`, `installer_params`, `fallback_chain`, `package_versions`) ne passent pas à l'échelle pour 10 000+ outils avec multiples versions/package-managers/plateformes.

**Décision à prendre** :
- Stocker les définitions complètes dans des fichiers YAML externes (recipe URL)
- Tout gérer via BDD distante (versionne les recettes dans le serveur catalogue HTTP)
- Mixte : BDD locale légère + sync distant, recettes en YAML versionné (git)

→ Voir `V0.5.8` qui est consacré à cette résolution.

### Périmètre
- Catalogue (browse, search, filtre)
- Outils locaux (état, version)
- Installateur (lancer, suivre, annuler)
- Installateur de modèles via Ollama ou block
- Logging et feedback visuel

### Sous-versions
**V0.5.0** — Socle Tauri + bridge Python ✅
- [x] Projet Tauri v2 init (React + Tailwind + Rust)
- [x] Rust `main.rs` : bridge Python (check, catalogue, install via `std::process::Command`)
- [x] Scripts Python : `scripts/check.py`, `scripts/catalogue.py`, `scripts/install.py`
- [x] Panel System Check (matériel, dépendances, outils installés)
- [x] Panel Catalogue groupé par classes (collapsible), trié par `sort_order`
- [x] Bouton install one-click par outil
- [x] Table `tool_classes` en BDD + seed des classes
- [x] `gui/installer/src-tauri/target/` retiré de l'historique git
- [x] Binaire compilé : `modelweaver-installer` (release)
- [x] Search/filtre dans le catalogue
- [x] Indicateur de progression pendant l'installation
- [x] Logs temps réel dans l'UI

**V0.5.1** — Vue catalogue enrichie ✅
- [x] Barre de recherche avec debounce
- [x] Filtres par classe, statut (installé/non)
- [x] Détail d'outil (version, description, dépendances)
- [ ] Rafraîchissement du catalogue depuis le serveur distant
- [ ] Cache local des données catalogue

**V0.5.2** — Vue outils locaux + installer ✅
- [x] Scan des outils installés (version, chemin, statut)
- [x] Install/Uninstall depuis l'UI — install OK, uninstall OK
- [x] Suivi de progression (barre de progression + logs temps réel via Tauri Events)
- [x] File d'attente d'installation multiple (séquentiel via batch_install.py)
- [x] Gestion des erreurs (timeout configurable par `--timeout=`)

**V0.5.3** — Vue modèles (Ollama) ✅
- [x] Liste des modèles Ollama installés
- [x] Bouton pull (streaming) + remove
- [x] Statut de téléchargement (progression)
- [x] Suppression de modèle

**V0.5.4** — Détection gestionnaires de paquets OS
- [ ] Détection automatique de tous les gestionnaires installés (apt, snap, brew, winget, choco, pacman, yay, dnf, yum, zypper, apk, emerge, nix, flatpak, pip, cargo, npm, go)
- [ ] Table `package_managers` en BDD + seed
- [ ] Panel UI listant les gestionnaires détectés
- [ ] Enrichir les outils avec versions alternatives par gestionnaire
- [ ] UI : sélecteur de version quand plusieurs disponibles
- [ ] Dispatch install/uninstall vers le bon gestionnaire

**V0.5.5** — Résolution du stockage des définitions d'outils ✅
- [x] Décision : format YAML externe (`.mw.yaml`) dans `install_recipe/`
- [x] Spec complète : `install_recipe/spec.txt` (manager blocks, install/uninstall, variables, fallback)
- [x] 9 recettes créées (curl, git, gitingest, litellm, modelweaver, ollama, opencode, open-webui, python3)
- [x] `index.mw.json` : index des recettes avec version recommandée
- [x] `RecipeParser` : parse YAML + résolution OS/manager fallback + exécution install/uninstall
- [x] BDD : migration `tools.recipe_path` (ALTER TABLE) + ToolRepository.save() mis à jour
- [x] `Installer.install()` : utilise la recette en priorité si `recipe_path` présent, fallback legacy sinon
- [x] `Installer.uninstall()` : idem
- [ ] UI inchangée (backend uniquement)

**V0.5.6** — Catalogue enrichi : ajouter un nouvel outil ✅
- [x] Formulaire UI pour créer un outil non présent
- [x] Sauvegarde en BDD via add_tool.py
- [x] Apparition dans le catalogue avec bouton install actif

**V0.5.7** — Vérification espace disque avant installation ✅
- [x] `bin/modelweaver-install` : CLI wrapper pour installer/désinstaller par ref ou --recipe
- [x] `test_disk_space.py` : mesure size_download et size_disk dans Docker `mw-v0.5.7`
- [x] Base `ubuntu:24.04`, `du -sb /` avant/après install + cleanup
- [x] Met à jour le `.mw.yaml` avec `size_download` et `size_disk`
- [x] `keep_cache` paramètre de l'Installer (CLI flag --keep-cache)
- [ ] Afficher un warning dans le GUI si espace insuffisant (futures V0.5.x)

**V0.5.8** — Tests GUI automatisés (dépriorisé)
- [ ] Tests unitaires composants React
- [ ] Tests d'intégration Rust → Python
- [ ] Test E2E check → catalogue → install → vérification

---

## V0.6 (Planifiée 📋) — GUI Agencement des Rôles

**Objectif** : Interface visuelle pour composer les rôles (blocks, drag-and-drop).

### Périmètre
- Éditeur de rôles en blocks visuels (pipeline → blocs)
- Bibliothèque de roles existants
- Prévisualisation du YAML généré
- Import/export de rôles

### Sous-versions
- **V0.6.0** — Wireframe + block library
- **V0.6.1** — Drag-and-drop pipeline
- **V0.6.2** — Prévisualisation YAML
- **V0.6.3** — Import/export rôles
- **V0.6.4** — Tests GUI

---

## V0.7 (Planifiée 📋) — GUI Définition d'Agent

**Objectif** : Interface visuelle pour créer, configurer et déployer un agent.

### Périmètre
- Création d'agent (nom, rôle, provider, limites)
- Configuration du workflow
- Branchements (connect à chatroom/todo/queue/agent)
- Déploiement (compile the YAML → BDD)
- Monitoring de l'agent (status, messages, sessions)

### Sous-versions
- **V0.7.0** — Wireframe + création d'agent
- **V0.7.1** — Config workflow visuel
- **V0.7.2** — Branchements visuels
- **V0.7.3** — Déploiement + monitoring
- **V0.7.4** — Tests GUI

---

## V0.8 (Planifiée 📋) — Dashboard

**Objectif** : Tour de contrôle simple — voir et piloter les agents en cours.

### Périmètre
- Vue d'ensemble des agents (statut, activité)
- Play/Stop/Restart des agents
- Logs en temps réel
- Monitoring ressources (CPU, RAM)
- Vue tâches partagées + chatroom

### Sous-versions
- **V0.8.0** — Wireframe + vue d'ensemble
- **V0.8.1** — Contrôles Play/Stop/Restart
- **V0.8.2** — Logs temps réel
- **V0.8.3** — Monitoring ressources
- **V0.8.4** — Tests dashboard

---

## V0.9 (Planifiée 📋) — Test Complet

**Objectif** : Test de bout en bout — de l'installation au déploiement d'agents.

### Périmètre
- Test installation complète via GUI V0.5
- Test création de rôles via GUI V0.6
- Test déploiement d'agent via GUI V0.7
- Test monitoring via dashboard V0.8
- Test orchestration multi-agents sur tâche réelle
- Validation performances et robustesse

### Sous-versions
- **V0.9.0** — Scénarios de test E2E
- **V0.9.1** — Test installation GUI
- **V0.9.2** — Test création rôles + agents GUI
- **V0.9.3** — Test orchestration multi-agents
- **V0.9.4** — Validation finale + documentation
