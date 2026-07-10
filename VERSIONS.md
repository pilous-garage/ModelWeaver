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

## V0.6 — Framework d'Agent (🔜 Prochaine)
**Objectif** : Framework pour définir et orchestrer des agents.

- Gestionnaire de clés (Key Vault) : chiffrement AES-256-GCM, stockage sécurisé
- Abstraction LLM (LLM Bridge) : interface unifiée `BaseProvider` (chat, embed, list_models)
- Adaptateurs : Ollama, LiteLLM, APIs natives
- Hardening du système : sandboxing des commandes, logging structuré
- Interface de configuration des rôles d'agents
- Import/export de rôles

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

## V1.0 — Release Stable (🎯 Objectif)
**Objectif** : Version publique distribuable, stable et documentée.

- Tests E2E complets
- Portabilité Windows
- Branding final
- Documentation professionnelle
- Campagne de lancement
