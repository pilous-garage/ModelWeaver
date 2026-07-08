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

## V0.3 (En cours 🚧) — Intégration SQLite Complète

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
- [ ] Définition complète du schéma SQLite (tables, indexes, contraintes)
- [ ] Script de migration des JSON → SQLite
- [ ] Tests de validation du schéma

**V0.3.1** — Module catalogue porté sur SQLite
- [ ] Remplacement des JSON par des queries SQLite
- [ ] Sync catalogue distant → local

**V0.3.2** — Module key_manager porté sur SQLite
- [ ] Stockage des clés en base avec chiffrement
- [ ] Tags free/payant, dates péremption

**V0.3.3** — Module checker/installer porté sur SQLite
- [ ] État système dans SQLite
- [ ] Traçabilité des installations

**V0.3.4** — Module plumber porté sur SQLite
- [x] Constructeurs des repositories sans arguments utilisés
- [ ] Limites, rate limits, quotas lus depuis la BDD
- [ ] Routage basé sur les données BDD

**V0.3.5** — Catalogue distant + synchro HTTP
- [x] `sql/catalogue_server.py` : serveur HTTP sur port configurable
- [x] `CatalogueDB.sync_from_url()` : sync toutes les tables depuis une URL
- [x] `CatalogueDB._ensure_schema()` : auto-création des tables si vide
- [x] `install_in_docker.py` : version SQLite (utilise `ModelWeaverDB` + `CatalogueDB`)
- [x] `build-docker.sh --sqlite` : copie `catalogue.db` → `catalogue.remote.db`, démarre le serveur, injecte `CATALOGUE_URL` dans le container

**V0.3.6** — Nettoyage et finalisation
- [ ] Suppression des fichiers JSON obsolètes
- [ ] Tests d'intégration complets (Docker --sqlite)
- [ ] Documentation du schéma

---

## V0.4 (Planifiée 📅) — Agent Factory & Orchestration

**Objectif** : Factory d'agents spécialisés, orchestration multi-agents, exécution planifiée.

### Sous-versions

**V0.4.0** — Agent Factory
- [ ] Définition des types d'agents (code, review, debug, search, etc.)
- [ ] Création dynamique d'agents avec prompts et outils configurables

**V0.4.1** — Orchestration multi-agents
- [ ] File d'attente, priorisation, exécution parallèle
- [ ] Communication inter-agents

**V0.4.2** — Planification et automatisation
- [ ] Tâches planifiées (cron-like)
- [ ] Pipelines de traitement configurables
