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

### V0.6.1 — Clôture V0.6.0 + durcissement clés 🔒 (en cours)
- **Finitions gestionnaire de clés** : feedback UX sur ajout (message d'erreur
  visible au lieu de retour silencieux), auto-sélection du 1er provider,
  affichage des endpoints par provider.
- **Nettoyage roadmap** : ce document reflète enfin l'état réel de V0.6.0.

### V0.6.x — LLM Bridge + adaptateurs + rôles 📝 (planifié, après V0.6.1)
- **Abstraction LLM (LLM Bridge)** : interface unifiée `BaseProvider` (chat,
  embed, list_models) — catalogue LLM déjà en place.
- **Adaptateurs** : Ollama, LiteLLM, APIs natives (tous consomment
  `KeyManager.get_key`).
- **Hardening** : sandboxing des commandes, logging structuré.
- **Interface config rôles d'agents** + import/export de rôles.

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
