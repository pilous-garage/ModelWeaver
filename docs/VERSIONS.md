# Versions — Périmètres et Limites

## V0.1 (Terminée ✅)
Bootstrap minimal : `modelweaver.sh` (sh portable) + `modelweaver.py` (audit, cache, installation).

## V0.2 (Terminée ✅)
Architecture modulaire en 3 couches / 9 modules :
1. **Catalogue** — source de vérité JSON
2. **Key Manager** — coffre-fort + onboarding
3. **Checker** — inspection système
4. **Installer** — préparation environnement
5. **Container Manager** — orchestration Docker
6. **Test Runner** — validation scripts agents
7. **Plumber** — routeur d'API intelligent
8. **Organiser** — interface CLI/TUI
9. **Dashboard** — tour de contrôle

## V0.3 (Terminée ✅)
Intégration SQLite : `modelweaver.db` (12 tables) + `catalogue.db` (4 tables).
- DAO/Repository pattern dans `sql/`
- Catalogue distant HTTP + synchro (`catalogue_server.py`)
- Installer DB-driven, `manifest.json` v2.0

## V0.4 (Terminée ✅)
Intégration Docker :
- `install_in_docker.py` avec support SQLite
- `build-docker.sh --sqlite` : copie catalogue + serveur distant
- `ModelWeaverDB._ensure_schema()` auto-création

## V0.5 — GUI Installateur (Terminée ✅)

### V0.5.0 — Pivot Tauri
- Décision d'abandonner l'ancienne GUI Python au profit d'une app native Tauri (Rust + React/TypeScript).
- `projetadmin/gui/` archivé en `projetadmin/gui-first-try/` (source de référence).

### V0.5.1 — Architecture 2 apps
- Split en **2 binaires** dans un même projet :
  1. `modelweaver-bootstrap` — télécharge le release, dépaquette, lance la suite
  2. `modelweaver` — l'application principale (vérification dépendances, dashboard)
- `projetadmin/gui-bootstrap/` et `projetadmin/gui-main/` — chacun un projet Tauri standalone.

### V0.5.2 — Bootstrap : Backend Rust
- 6 commandes Tauri : `get_platform`, `check_update`, `self_update`, `download_release`, `unpack_release`, `launch_main`.
- Vérification de nouvelle version bootstrap sur GitHub Releases (OS + arch).
- Auto-update : télécharge et remplace le binaire courant.

### V0.5.3 — Bootstrap : Frontend React
- UI moniteur (barre de progression + logs en direct).
- Flux automatique : init → check update → download → unpack → launch.

### V0.5.4 — Main : Backend Rust
- 4 commandes Tauri : `get_system_info`, `check_dependencies`, `install_dependency`, `run_python_script`.
- Vérifie Python, SQLite, Git (version + minimum requis).
- Installation automatique via apt/brew selon OS.

### V0.5.5 — Main : Frontend React
- 3 modes : CHECKING → INSTALLING → DASHBOARD.
- Auto-détection des dépendances manquantes.
- Bouton d'installation par dépendance.
- Re-vérification après installation.

### V0.5.6 — Outils Build
- `build-bootstrap.sh` mis à jour : build des 2 apps séquentiellement.
- Correction PATH pour cargo dans les sous-shells npm.
- Génération d'icônes PNG blue placeholder.

### V0.5.7 — Build Multi-apps ✅
- Bootstrap : binaire 14 Mo, 3 bundles (.deb, .rpm, .AppImage).
- Main : binaire 14 Mo, 3 bundles (.deb, .rpm, .AppImage).
- Fenêtres : bootstrap 700×500, main 1000×700.
- Release GitHub v0.5.7 publiée (8 assets).

### V0.5.8 — Nettoyage & Fix Docker (Terminée ✅)
- Nettoyage git history (1.4G → 836K) via git-filter-repo
- Fix path dans `gui_helper.py` (PROJECT_ROOT 2 dirname)
- Fix `sql/db.py` (imports optionnels libsql_client, dotenv)
- Première version des scripts de test CLI/GUI Docker

### V0.5.9 — Tests CLI/GUI robustes (Terminée ✅)
- `test-cli.sh` : rewrite avec `--docker`, `--timeout=N`, tag Y/N/U, log auto horodaté, auto-save durée
- `test-gui.sh` : rewrite avec `--timeout=N`, tag Y/N/U, log auto, détection X11, docker run -d + timer
- Fix timeout docker : utilisation de `docker run -d` + timer shell au lieu de `timeout` (évite D state)
- Fix buffering : `exec > >(tee ...)` global au lieu de `| tee` dans les pipes
- CLI test réussi en Docker vierge (85s, exit 0)
- GUI test timeout fonctionnel (15s, exit 124, logs capturés)
- `.gitignore` mis à jour (logs, .last-test-*-time)
- Retrait des scripts obsolètes (get-log-docker-test.sh, Dockerfile.test)

### V0.5.17 — Service testeur E2E ✅
- Service `tester` : lit `~/.modelweaver/tests/test-script.txt`, exécute install/uninstall/reinstall, génère `test-report.txt`.
- Résumé de l'interface services (catalogue, installer_worker, tester, watch_installed, watch_sysstate).
- Bump version 0.5.17.

### V0.5.18 — API locale decouplée ✅
- Découlpage UI ↔ backend via daemon HTTP/JSON local (127.0.0.1, token `~/.modelweaver/api.token`).
- `services/api/` : `daemon.py` (20 routes), `client.py` (SDK Python), `cli.py`.
- `ARCHITECTURE_API.md` : contrat, sécurité, design SDK.
- Hard-check des contrats (`hardcheck/verify.py`) : routes interface↔impl, déps, frontières AST.
- Migration de 11 modules + 6 services vers `modules/`/`services/` avec `_contract/`.
- Bump version 0.5.18.

### V0.5.19 — Réorganisation workspace + data-layer en module ✅

### V0.5.20 — Décomposition gui_helper + superviseur single-instance ✅
- Décomposition du monolithe `gui_helper` : logique métier extraite dans `services/*`
  (installer_worker : file install_jobs + install/uninstall ; tester ; watch_installed ;
  watch_sysstate ; catalogue) et `modules/*` (catalogue, checker, sql).
- `services/_common.py` : `_db_paths`, `_quiet_stdout`, `log_to_file`, `acquire_instance_lock`
  (single-instance par service via lockfile `~/.modelweaver/run/<name>.pid`).
- Daemon API (`services/api/daemon.py`) ne dépend plus de `gui_helper` : consomme
  directement `modules.*` + `services.installer_worker.jobs`.
- `gui_helper.py` réduit à un shim fin (compat Tauri) déléguant aux modules/services.
- Superviseur Rust : `define_service` repointés sur `services/*/service.py` + daemon API
  (`serve`); **anti-double-instance** : un seul processus par service (et le superviseur
  lui-même) via verrous `kill -0`. Bump 0.5.20.

- Réorganisation racine : `docs/` (docs suivies), `docker/`, `autoanalyse/`, `oldcode/` (gitignorés, archives).
- Retrait de `projetclient/` : pont `sys.path` démonté (`gui_helper`, `hardcheck`), `legal/TOS.md` → `projetadmin/legal`, reste archivé.
- `sql/` promu module normal : `modules/sql/` (imports `modules.sql.*`), contrats mis à jour.
- `~/.modelweaver` comme emplacement unique des DB (no-arg et GUI alignés).
- `.gitignore` renforcé (`.pytest_cache`, etc.) ; dépôt clône < 3 Mo.
- Bump version 0.5.19.

### V0.5.21 — Fix seed catalogue + Docker E2E complet ✅
- Correction du `seed_catalogue` : remplacement `os.path.dirname(os.path.dirname(__file__))`
  (`services/`) par `_REPO_ROOT` (bonne détection de la racine).
- Correction `op_deps_check` : `REPO_ROOT` → `_REPO_ROOT` (NameError latent).
- Test HTTP curl corrigé : `-X POST` au lieu de GET par défaut pour les routes API.
- E2E Docker : 13/13 tests OK (CLI + HTTP avec/sans token).
- Nettoyage Docker : suppression image obsolète `modelweaver-gui:latest` (14.3GB).
- Bump version 0.5.21.

### V0.5.22 — Auto-install Rust + DB singleton ✅
- Auto-install Rust au démarrage du daemon (thread différé) : `POST /v1/tools/install/all`
  indépendamment du webview Tauri. Les 3 outils (opencode, litellm, keyring) sont mis en file.
- DB connection management : singletons partagés (`_get_mw()`, `_get_cat()`) avec
  double-checked locking thread-safe — élimine la majorité des conflits de verrouillage.
- Background processor : job atomique (lock → SELECT+UPDATE → unlock), lock relâché
  pendant l'install longue ; correction double-release bug.
- Bump version 0.5.22.

## V0.6 — Framework d'Agent

**Objectif** : poser les briques de sécurisation des clés et du catalogue LLM
avant d'orchestrer les agents.

> ⚠️ **Palier test/UI V0.6.0** : un **test complet + paufinnage visuel** de V0.6.0
> est requis (GUI clés : slider, badge 🔒, masque ; démonstration end-to-end du
> verrouillage refusant bien l'accès plaintext aux agents). — **[Clôturé en V0.6.1.0]**
> Le paufinnage visuel (slider/verrou) et la démo E2E conteneur ont été validés.

### V0.6.0 — Key Manager : keyring + verrouillage manuel ✅
- **Stockage keyring OS** : clés jamais en clair sur le disque (GNOME Keyring /
  macOS Keychain / Windows Credential Manager) ; fallback headless Fernet dérivé
  du `machine-id`.
- **DB = métadonnées pures** : `api_keys` ne contient plus que provider, tag,
  grade, santé, timestamps + `ref` (UUID unique non réécrivable). `key_display`
  dérivé EN MÉMOIRE (jamais stocké).
- **Verrou manuel par clé** : `locked` persisté ; `get_key()` lève `KeyLockedError`
  si verrouillée. Slider lock/unlock dans la GUI.
- **Chargement en mémoire** : `load()` peuplé le cache après validation du keyring.
- Routes API : `keys/set`, `keys/get`, `keys/list`, `keys/delete`, `keys/set_lock`,
  `keys/onboard`. Hard-check des contrats vert (103 vérifications).
- Suppression de l'ancien module `modules/security/vault.py`.

### V0.6.0.x — Catalogue providers + endpoints + restriction modèles (hors-périmètre, fait)
Travaux ajoutés au-delà du Key Manager, validés E2E conteneur :
- **Catalogue providers complet** : seed SQL de **154 providers** (cloud/local/ollama/builtin)
  dans `catalogue_schema.sql` (`INSERT OR IGNORE`, idempotent). `_ensure_schema`
  réexécute le seed si la BDD pré-existante est vide. `sync_providers` désormais
  no-op (seed en SQL). `sync_models` accepte `id` (rétro-compat).
- **Table `provider_endpoints`** (provider → N endpoints) : `endpoint_id` PK,
  `provider_id` FK, `label`, `endpoint_url`, `api_type`, `is_default`,
  **`local_latency` REAL**, **`global_quality` REAL**. Seed de 22 endpoints
  canoniques (openai, anthropic, google/gemini, groq, mistral, deepseek, cohere,
  openrouter, together, fireworks, perplexity, nvidia, huggingface, github-models,
  scaleway, ovhcloud, ollama, lmstudio, + bases templatées azure/bedrock/vertex/
  databricks). `op_providers_list` expose `endpoints[]` ; route `provider/endpoint/add`.
- **Restriction fetch modèles** : `op_llm_models_list` / `op_llm_recommend` ne
  retournent les modèles que pour les providers **ayant une clé API** (ou
  ollama/builtin/local, sans clé requise). Provider sans clé → `error: no_api_key`.
- **Onboarding `.env`** : `GOOGLE_GEMINI_API_KEY` → provider `google`
  (corrigé, était `gemini`).
- **GUI** : gestionnaire de providers (connu / nouveau), bouton « 🔄 Fetch modèles »
  filtré par provider avec clé, feedback d'erreur visible sur le bouton « + ».

### V0.6.1 — Clôture V0.6.0 + durcissement clés 🔒 ✅
- **Finitions gestionnaire de clés** : feedback UX sur ajout (message d'erreur
  visible au lieu de retour silencieux), auto-sélection du 1er provider,
  affichage des endpoints par provider.
- **Nettoyage roadmap** : ce document reflète enfin l'état réel de V0.6.0.

### V0.6.2 — Fix IPC GUI (fetch → daemon_post) ✅
- **Remplacer fetch() direct par invoke('daemon_post')** : les appels HTTP du
  WebView Tauri vers le daemon étaient bloqués par la CSP (Content Security
  Policy) par défaut (« Load failed »). Solution : passage par le pont Rust
  `daemon_post` qui utilise `curl` en interne, bypassant CSP et CORS.
- **CSP dans tauri.conf.json** : sécurisation préventive.
- **Refonte complète des handlers** : `handleSetKey`, `fetchKeys`,
  `fetchModels`, `handleAddProvider`, `handleDeleteKey`,
  `handleToggleLock`, `fetchDbVersion`.

### V0.6.3 — LLM Bridge : abstraction unifiée des providers ✅
- **Interface `BaseBridge`** (`modules/llm_manager/base_bridge.py`) : contrat commun
  (chat, chat_stream, get_capabilities, list_available_providers/models,
  health_check, classify_error) + types partagés (ModelCapabilities,
  ChatResponse, ErrorCategory, BridgeError(Exception)).
- **`LiteLLMBridge`** (`litellm_bridge.py`) : 1 adaptateur (litellm) ;
  ErrorClassifier (auth/rate/context/server/timeout/unknown) ; ContextValidator
  (cache + persistance BDD + audit log `context_audit_log`).
- **Streaming SSE** : `op_llm_chat_stream_sse` + `STREAMING_ROUTES` +
  `StreamWriter` dans le daemon ; route `auth/info` (bootstrap token/port
  pour `fetch()` direct côté GUI). Routes `llm/chat/stream` validées E2E.

### V0.6.4 — Gestionnaire LLM locaux ✅
- **`LocalEngineManager`** (`modules/llm_manager/local_engines.py`, NOUVEAU) :
  détection live Ollama (:11434 / `/api/tags`), LM Studio (:1234 /
  `/v1/models`), llama.cpp (:8080) ; start/stop headless (Ollama via
  subprocess + PID) ; listage modèles via API locale. Singleton partagé.
- **Routes daemon** : `llm/local/list`, `llm/local/start`, `llm/local/stop`,
  `llm/local/models`.
- **GUI** : onglet « LLM locaux » (état moteurs, boutons
  démarrer/arrêter, modèles détectés).
- **Test E2E conteneur** : détection Ollama (port + `/api/tags`), listage
  modèles, routage bridge `provider=ollama` → `localhost:11434` validés via
  moteur local mock + intégration `llm/chat`. Pull d'un vrai minimodèle
  (smollm:135m) non effectué : bande passante conteneur ~400 KB/s
  (~1 Go → ~50 min).

### V0.6.5 — Agent Architecture (définition) ✅
- **Spécification complète** (`docs/AGENT_SPEC.md`, `docs/AGENT_SPEC_FINAL.md`) :
  Agent = automate à états, LLM = tool provider, occupation continue/noncontinue/disparate.
- **Déclaration évolutive** : noyau (name+role+occupation) + extensions
  (personality, context, variables, FSM, resources, channels, signals, tools).
- **Resources extensibles** par booléen + ordres de grandeur (ram, cpu, llm...).
- **Priorité 0-10 + préemption** 3 niveaux (stop→kill→pkill).
- **Architecture Phénix++** : threads hydratés/déshydratés, OS gère le multithreading.
- **Agent Manager** supervise (existence, heartbeats, signaux), n'orchestre pas.
- **Ticker** : un seul point de contact → vérifie l'Agent Manager.
- **Organisateur** : assigne les LLM au runtime, l'agent ne choisit pas.
- **Signaux parallèles** : pause/status/kill sans interruption forcée.

### V0.6.5.1 — Agent Core (Phase 1 : minimal) ✅
- Nouvelle table `agents` (occupation, resources_json, variables_json).
- Table `agent_runtime` (thread_id, pid, heartbeat).
- `Agent.hydrate()` / `Agent.dehydrate()` — cycle BDD → thread → BDD.
- `Agent.execute()` — appel Bridge LLM (pas de FSM compliqué encore).
- `AgentManager` — `list_active()`, `check_heartbeats()`.
- Ticker → check Agent Manager uniquement.

### V0.6.5.2 — FSM Interpreter (Phase 2) ✅
- Remplace l'ancien Worker.
- Exécute les steps : llm_call, tool_call, switch, sleep, set_variable, end.
- LLM Bridge devient un tool provider (plus de urllib.request direct).
- Migration des rôles YAML existants vers le format FSM.

### V0.6.5.3 — Resources & Priorité (Phase 3) ✅
- **Organisateur** (`modules/llm_manager/organisateur.py`) : logique PURE d'allocation LLM. Reçoit `resources.llm` + `llm_pref {provider, model, use_case, level}`, matche providers à clé (KeyManager) + moteurs locaux (Ollama/LM Studio/llama.cpp), fallback recommandation catalogue. Le LLM est un tool provider → l'Organisateur l'assigne, l'agent ne choisit pas.
- **Ressource Manager** (`services/ressource_manager/`, service global) : agrège Checker hardware (RAM/Disque/**CPU global**) + LLM (Organisateur) + futurs gestionnaires. `evaluate(resources)` → verdict `possible`/`impossible` + raisons + allocation LLM. `watch_resources()` publie le snapshot.
- **AgentManager** : `evaluate(agent_id)` (verdict ressources+LLM) et `admit(agent_id)` (admission control + **préemption 3 niveaux stop→kill→pkill** des agents `preemptible` de priorité strictement inférieure). `resources.priority` (0-10) et `resources.preemptible` actifs.
- **Daemon** : routes `agent/resources/evaluate` + `agent/admit` ; `agent/execute` auto-alloue le LLM via l'Organisateur quand non imposé. Contrats MAJ.
- Correction import circulaire `agents/__init__` ↔ `agent_manager.service` (import lazy d'`AgentManager` dans `ticker.py`).
- Testé : allocation LLM (mock Ollama → `ollama/codestral`), évaluation ressources (RAM/CPU), admission + préemption (kill d'un agent basse priorité confirmé).

### V0.6.5.4 — Signaux & Streaming (Phase 4) ✅
- **Canal de signaux** (`agent_signals`) : `AgentManager.send_signal/pending_signals/ack_signal/complete_signal`. Types `pause, resume, status, health, kill, configure`.
- **Consommation par l'agent** (FSM) : `signal_check` appelé avant chaque étape (et en boucle pendant une pause) ; `pause`→attente jusqu'à `resume` ; `kill`→`AgentAbort` (aussi **en cours de génération LLM**, chunk par chunk) ; `configure`→fusionne `variables` + persiste `state_json` ; `status`/`health`→acquittés.
- **Streaming bus** (`agents/stream_bus.py`) : buffer circulaire par agent ; le FSM diffuse chaque chunk `llm_call` (via `bridge.chat_stream`) ; route `agent/stream` (poll `seq`) pour visualisation temps réel.
- **Interjection** : un signal envoyé en cours d'exécution (ex. `kill` mid-stream) est consommé et interrompt l'agent.
- **Daemon** : routes `agent/signal`, `agent/signals`, `agent/signal/ack`, `agent/signal/complete`, `agent/stream`. Contrats MAJ.
- **GUI** : onglet 🤖 Agents (liste + boutons Pause/Reprendre/Config/Kill/Stream + viewer de stream temps réel pollé).
- Correction précédence LLM : le `model_ref` déclaré à l'étape FSM prime sur l'allocation par défaut de l'Organisateur.
- Testé : pause/resume (success), kill (aborted, y compris mid-stream), configure (variables injectées + persistées), streaming (10 chunks via `agent/stream`), interjection kill mid-stream.

### V0.6.5.5 — Agents Rares & Orchestration (Phase 5) ✅
- Occupation `disparate` : spawn à la demande (`AgentManager.spawn_agent`), exécution puis sommeil BDD automatique (`state.sleeping=true`, pas de runtime résident). Testé : agent créé exécuté → status `INIT`, runtime absent.
- `spawn` step dans le FSM : `_step_spawn` (`spawn_handler`/`handoff_handler` injectés dans `Agent.execute`) crée un agent enfant, capture sa sortie (`output_capture`), nom auto-uniquifié en cas de collision. Testé : enfant `p5_grand` créé + réponse LLM capturée ("Bien sûr ! ... Luna").
- Succession : `AgentManager.handoff()` transfère variables + state_json de `from`→`to`, chaîne `successor_id`, `from` retourne INIT/dort. Testé via `agent/handoff` : var `child_out` transportée vers le successeur.
- **Correction** : `spawn_agent` encapsule un `config` de type workflow (`{"steps":...}`) en `{"workflow": ...}` pour qu'`Agent.execute` le reconnaisse (sinon l'enfant tombait en chemin Phase 1 sans modèle).
- **Daemon** : routes `agent/spawn`, `agent/handoff` + contrats MAJ (`services/api/_contract/interface.py`).
- **GUI** : onglet 🤖 Agents — tableau de bord Agent Manager (compteurs `● Actifs` / `🧟 Zombies` depuis `agent/manager/status`) + badge `● actif` / `🧟 zombie` par agent + heartbeat (ms) et `🪜 current_step` quand running.
- **Bug fix critique** : `AgentManager.kill()` enregistrait `pid = os.getpid()` (le daemon lui-même, car les agents s'exécutent inline) et faisait `os.kill(pid, SIGTERM)` → un `kill`/`admit` sur un agent actif tuait le **daemon**. Désormais, si `pid == os.getpid()` (agent inline), `kill()` enfile un signal `kill` que la FSM honore (`AgentAbort`) au prochain `signal_check`, au lieu de SIGTERM/SIGKILL le daemon.
- **E2E Docker** : test complet `tests/e2e_agent_framework.py` piloté via le client HTTP réel (`MWClient`) contre le daemon, conteneurisé (`docker/Dockerfile.e2e` + `docker/entrypoint-e2e.sh` + `docker/run-e2e-agent.sh`). `--network host` pour joindre l'Ollama de l'hôte (modèles en cache). `.env` filtré (hors opencode/zen, openrouter, nvidia) onboardé. **Résultat : 29/29 PASS** (Phases 1→5 + cloud GROQ réel).

### V0.6.6 — Chat = agent pur (framework Agent) ✅
Le chat est une **exécution agentique standard** du framework (V0.6.5) :
chaque session de chat = un **agent** (`role_type='chat'`,
`occupation='noncontinue'`) exécuté via `FSMInterpreter` avec un workflow
mono-étape `CHAT_WORKFLOW` (1 step `llm_call`), historique persisté dans
`variables_json.messages`, streaming via `StreamBus`, signaux via
`_make_signal_check`. Aucune logique LLM dupliquée hors du framework.

- **`services/chat/` supprimé** : `ChatService` dupliquait la logique LLM du
  framework → retiré. Le chat est 100% porté par le framework Agent.
- **`Agent.chat_turn()`** (`services/agent_manager/service.py`) : exécute
  `CHAT_WORKFLOW`, publie les tokens sur le `StreamBus` (clé agent_id),
  persiste l'historique (`variables_json.messages`) et `_reply` dans la BDD.
- **`AgentManager`** : `create_chat_session` / `list_chat_sessions` /
  `get_chat_session` / `update_chat_session` / `delete_chat_session` /
  `chat_send` / `chat_read` (facades qui pilotent des agents chat).
- **Routes daemon** `chat/session/*` (create/list/get/update/delete/send/
  history/read/stream) = **façades** sur `AgentManager` (aucune dépendance à
  un service dédié ; réutilisent `agent/*`, `agent/stream`, signaux).
- **Streaming** : tokens publiés sur le `StreamBus` (clé agent_id),
  consultables via `chat/session/stream` (ou `agent/stream`).
- **Isolation** : une session n'écrit QUE dans sa propre histoire ; lecture
  des autres sessions conditionnée à `allow_read_others`.
- **Bug corrigé** : `chat_turn` fusionnait `result.variables` (copie prise à
  l'entrée de `run()`, avec `messages=[]`) APRÈS avoir positionné
  `variables["messages"]` → écrasait l'historique. Correction : fusion des
  variables du FSM PUIS réécriture de `messages = new_history`.
- **E2E** : `tests/e2e_agent_framework.py` Phase 6 = **38/38 PASS** (sessions
  simultanées, LLM local Ollama, streaming, read-others autorisé/refusé,
  persistance historique user+assistant).

> Reste à faire (GUI) : fenêtre de chat Tauri (sélecteur modèle, historique,
> SSE, markdown/code highlighting, multi-modèle, params avancés, export).

### Restructuration du code des agents (séparation framework / catalogue)
- `agents/` → **`AgentFrameWork/`** : moteur générique (FSM, StreamBus, Ticker,
  Worker, Factory, Dispatcher, Scheduler, Provisioning, services watchdog/watcher/
  review/auto_debug, DSL/pipeline/tool executors).
- **`AgentsCatalogue/`** (nouveau) : contenu spécifique des agents :
  - `role_manager.py` (ex-`agents/role_manager.py`) + `rôles/` (12 YAML de rôles)
  - `skills/`, `personnalité/`, `comportement/`, `complet/` (dossiers data, vides pour l'instant)
- Tous les imports `from agents.` → `from AgentFrameWork.` (sauf `role_manager`
  → `from AgentsCatalogue.role_manager`). `ROLES_DIR` pointe sur `AgentsCatalogue/rôles`.
- `oldcode/` et `autoanalyse/` (copies legacy) laissés intacts (`from agents.` préservé).

### V0.6.7 — Rôles d'agents, configuration & routage dynamique ✅
**Agent Framework Daemon** : routage résolu à runtime (les agents sont
dynamiques — rôles, capacités, états, droits — donc les routes ne sont
PAS hardcodées au démarrage mais dérivées à chaque requête de
`(rôle → skills) × (état de l'agent)`).

- **`AgentFrameWork/router.py`** (nouveau) : cœur du routage dynamique.
  - `routes_for(role, state, skills)` → liste des `RouteSpec` qu'un agent
    expose **maintenant** (1 route par `skill` de rôle + ops lifecycle
    `status`/`configure`/`pause`/`resume`/`kill` selon l'état).
  - `resolve(agent, op)` → `(RouteSpec, None)` si autorisé, sinon
    `(None, raison)` avec `raison ∈ {unknown, not_capable, state}`.
  - `capabilities_catalog()` → catalogue des rôles + `skills`/`capabilities`
    déclarés dans `AgentsCatalogue/` (source de vérité des capacités).
- **Daemon — routes dynamiques `agents/{id}/*`** (vs `agent/*` statiques) :
  - `GET /v1/capabilities` → catalogue des rôles/skills.
  - `GET /v1/agents/{id}/routes` → introspection : ops que CET agent expose
    (dérivées de son rôle × son état).
  - `POST /v1/agents/{id}/<op>` → dispatch : op de capacité → `Agent.execute`
    (ou `chat_turn` pour `chat`) ; op lifecycle → signal/`configure`/`status`.
    Réponses : `200` autorisé, `403 not_capable` (skill hors-rôle),
    `404 unknown` (op inexistante), `409 state` (op lifecycle interdite à
    cet état), `405` méthode.
- **Résolution par skill** : un agent rôle `assistant` (skills `chat,
  research, summarize, search`) expose les routes `chat, research,
  summarize, search, status, configure` ; `code_gen` (skill absent du rôle)
  → `403 not_capable`.
- **E2E** : `tests/e2e_agent_framework.py` Phase 7 = **44/44 PASS** (catalogue,
  introspection routes, op autorisée exécute, op hors-rôle 403, op inconnue 404).
- **MWClient** : `request_raw(method, route, **params)` (retourne
  `(status, body)` sans lever sur 4xx, pour tester le routage).

> Reste (GUI) : Éditeur de rôles visuel (création/import-export JSON,
> bibliothèque de rôles), attribution provider-modèle par rôle.
> **Limite V0.6.7** : l'AFD est encore **en-process** (StreamBus mémoire,
> routage résolu dans le daemon REST) ; le split en processus dédié
> (StreamBus cross-process) est planifié en **V0.6.9**. Binaire GUI Tauri
> non rebuild (onglets chat/agents absents du binaire releasé).

- **Définition d'un rôle** :
  - Template de prompt système
  - Capacités associées (chat, code, analyse, search, tool_use)
  - Modèle(s) recommandé(s) par rôle
  - Niveau de température / params par défaut
- **GUI Éditeur de rôles** :
  - Création, modification, duplication, suppression
  - Import/export JSON
  - Bibliothèque de rôles pré-définis (assistant, codeur, relecteur,
    architecte, rédacteur, QA)
- **Attribution provider-modèle par rôle** : lier un rôle à un provider
  et un modèle spécifiques.
- **Tests de rôles** : chat de test direct depuis l'éditeur.

### V0.6.8 — Stockage disque propriétaire par agent (memagent) ✅
Chaque agent reçoit un dossier dédié `mw_home()/memagent/{agent_id}/` avec
quota soft, workspace RW complet, et escalade au gestionnaire de ressources
(demande d'augmentation → approbation utilisateur).

- **`AgentFrameWork/agent_storage.py`** (nouveau) : `AgentStorage(agent_id, conn)`.
  - Sous-dossiers : `mem/` (mémoire long-terme), `ctx/` (contexte),
    `history/` (historiques), `work/` (workspace dédié RW complet).
  - Quota par défaut **10 Mo** (configurable jusqu'à 10 Go pour docker),
    stocké dans `agents.storage_json.max_bytes`.
  - Compteur `used_bytes` mis à jour incrémentalement (léger).
  - `QuotaExceeded` levé si dépassement.
  - `request_quota_increase(needed)` enregistre une demande `pending`
    (escalade au gestionnaire de ressources → utilisateur).
  - `approve_quota_request(new_max)` approuve et met à jour le quota.
  - Cycle de vie : `ensure()` au hydrate, `destroy()` au delete.
- **BDD** : colonne `agents.storage_json` (TEXT, migration idempotente).
- **Nommage auto `role_N`** : si `name` non fourni → `assistant_3`
  (via `AgentManager._make_agent_name`). `role_type` reste séparé.
- **Routes dynamiques `agents/{id}/storage`** :
  - `GET` → infos (used/max/quota_request).
  - `POST` → recalc used (walk disque).
  - `POST agents/{id}/storage/quota/approve` → approuve demande pending.
- **E2E** : `tests/e2e_agent_framework.py` Phase 8 = **51/51 PASS**
  (auto-name, quota 10Mo, écriture, quota_request pending → approve,
  dossier détruit à la suppression).

### V0.6.9 — AgentDaemon : interface unique pour les agents ✅
Point d'entrée unique `AgentDaemon.call(agent_ref, function, **kwargs)` :
résolution agent (nom ou id) → routage (rôle + état via router.py) → exécution
via le framework. L'appelant ne sait rien de l'implémentation derrière.

- **`services/agent_daemon/`** (nouveau) : `AgentDaemon.call()` englobe toute
  opération sur un agent : lookup BDD, validation router, lifecycle (status/
  configure/pause/resume/kill), capability (chat/research/...), signaux,
  stream, spawn, handoff, evaluate, admit. Retourne toujours un dict
  `{"status": "ok"|"error", ...}`.
- **Routage dynamique** : `_agent_dynamic_route` dans `daemon.py` devient un
  **proxy mince** vers `AgentDaemon.call()`. Les opérateurs `agent/*` sont
  simplifiés (délégation).
- **Storage** (infra, pas agent) : reste dans `daemon.py` via `_storage_route`.
- **E2E** : 51/51 PASS (toujours vert après refactoring).
- **Prochaine étape** : extraction de l'AFD en processus dédié + StreamBus
  cross-process (socket Unix + BDD tmpfs WAL).

### V0.6.10 — Agent Framework Daemon : processus dédié ✅
Suite V0.6.9 (AgentDaemon en-process). Extraction du runtime des agents dans
un **processus dédié** distinct du gateway REST :

- **`services/afd/`** (nouveau) : processus standalone avec socket Unix IPC,
  Ticker asynchrone, auto-régénération au démarrage (sync agents table).
- **Socket Unix IPC** : `services/afd/ipc.py` — échange JSON ligne par ligne
  entre gateway et AFD. `AFDProxy` avec fallback local si AFD indisponible.
- **StreamBus cross-process** : `AgentFrameWork/stream_bus.py` — `StreamBusDB`
  SQLite WAL sur tmpfs (`/dev/shm/`). Singleton `stream_bus` dispatch auto
  entre mémoire et SQLite via `activate_cross_process()`. Zero contention
  (1 writer par agent_id).
- **Architecture 2-process** : gateway REST (daemon.py) + AFD (afd/service.py).
  Le superviseur Rust pourra lancer les 2 comme sidecars.
- **Résilience** : au démarrage, l'AFD appelle `AgentDaemon.sync()` (nettoie
  zombies, reprend les agents en cours). Si l'AFD crashe, la BDD persiste.
- **Route `GET /v1/capabilities`** déléguée à l'AFD.
- **E2E** : 51/51 PASS (tout vert en 2-process).

### V0.6.11 — Hardening et logging structuré 📝
- **Sandboxing des commandes** : exécution des outils et appels LLM dans
  un environnement restreint (sous-processus isolé, timeout, limite mémoire).
- **Logging structuré** : remplacement des `print()` par `structlog` /
  logging JSON (service, niveau, timestamp, message, contexte).
- **Rotation des logs** : pas de saturation disque.
- **Audit trail** : toutes les opérations sensibles (ajout/suppression clé,
  installation outils, exécution code) sont tracées.
- **Rate limiting** : protection des endpoints du daemon contre les appels
  abusifs.

## V0.7 — Sandbox de Création d'Agent (📝 Planifié)
**Objectif** : Studio visuel pour concevoir des workflows d'agents sans code.

- Éditeur de rôles low-code (designer de prompt, mapping de capacités)
- Factory d'agents (orchestrateur d'instances, health check)
- Branchements visuels (chatrooms, todo-lists, inter-agents)
- Déploiement et monitoring

## V0.8 — Organisateur Global & Framework (📝 Planifié)
**Objectif** : Dashboard central et tour de contrôle.

- Vue graphique (topologie, éditeur de pipeline)
- Monitoring temps réel (CPU/RAM, tokens, statut)
- Contrôles Play/Stop/Restart
- Logs en temps réel par agent/session
- Gestion des dépendances entre agents

## V0.9 — Mini-Entreprise de Création de Projet (📝 Planifié)
**Objectif** : Test réel complet : un projet logiciel conçu, développé et livré
par des agents ModelWeaver en autonomie, simulant une mini-entreprise.

- Définition d'un projet fil rouge (ex. générateur de sites statiques)
- Rédaction du cahier des charges par un agent rédacteur
- Découpage en tâches par un agent architecte
- Implémentation par des agents codeurs
- Revue de code et tests par un agent QA
- Livraison et documentation par un agent livraison
- Bilan : métriques de productivité, coûts tokens, temps réel

## V0.10 — Portage Windows / Fedora (📝 Planifié)
**Objectif** : Rendre ModelWeaver fonctionnel sur Windows (WSL + natif) et Fedora.

- Adaptation des chemins et variables d'environnement Windows
- Compatibilité shell (PowerShell / CMD)
- Tests sur Fedora (dnf, SELinux)
- Installation des dépendances natives par OS
- CI multi-OS

## V0.11 — Préparation Version Stable (📝 Planifié)
**Objectif** : Dernière ligne droite avant la release publique.

- Audit de sécurité complet
- Optimisation des performances (temps de réponse daemon, taille binaire)
- Relecture et mise à jour de toute la documentation
- Nettoyage dette technique (code mort, TODO, FIXME)
- Page de release + changelog

## V0.12 — Test Utilisateur (📝 Planifié)
**Objectif** : Test en conditions réelles par un utilisateur non-développeur.

- Installation et prise en main par un tiers (le frangin)
- Feedback brut : ce qui marche, ce qui bloque, ce qui manque
- Correction des blocages remontés
- Ajustements UX

## V0.13 — Décision Finale du Nom (📝 Planifié)
**Objectif** : Choisir le nom définitif du projet avant la release.

- Brainstorming de noms alternatifs à « ModelWeaver »
- Vérification disponibilité (npm, PyPI, GitHub, domaine)
- Vote utilisateur / communauté
- Renommage du projet si nécessaire

## V0.14 — Définition Complète CGU, Licence, Contrat d'Intention (📝 Planifié)
**Objectif** : Cadre légal complet avant mise à disposition publique.

- Rédaction des Conditions Générales d'Utilisation
- Choix et rédaction de la licence open-source
- Contrat d'intention pour les utilisateurs / contributeurs
- Mention légale et crédits
- Page « Legal » dans l'application

## V1.0 — Release Stable (🎯 Objectif)
**Objectif** : Version publique distribuable, stable et documentée.

- Tests E2E complets
- Portabilité Windows (via v0.10)
- Branding final (via v0.13)
- Documentation professionnelle
- Cadre légal finalisé (via v0.14)
- Campagne de lancement
