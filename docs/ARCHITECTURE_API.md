# ModelWeaver — Note d'architecture : API locale & découplage UI / backend

> Statut : **proposition de conception** (aucune migration engagée à ce stade).
> Objectif : figer le contrat avant de créer l'objet/API qui centralisera tout,
> puis migrer progressivement.

---

## 1. Principe directeur

**Le backend est l'unique source de vérité. Toute interface utilisateur (GUI, CLI,
TUI, web) n'est qu'un *client* de la même API locale.**

- Une **GUI = coquille vide** : elle ne fait que la *passerelle utilisateur ↔ machine*.
  Tout ce qui n'est pas une **configuration d'affichage** (taille de fenêtre,
  splashscreen, thème…) passe par l'API.
- Le **CLI est une interface au même titre que la GUI** : il tape la même API, pas
  du code métier dupliqué.
- Le backend tourne comme **daemon local**, indépendant de Tauri et de la version d'OS.

```
   ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌────────────┐
   │  GUI Tauri │   │  GUI Tauri │   │    CLI     │   │  Web /TUI  │   ← interfaces
   │     v2     │   │     v1     │   │            │   │            │      (clients)
   └─────┬──────┘   └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
         └────────────────┴───── même SDK client ───┴────────────────┘
                                   │
                    API locale (HTTP/JSON, 127.0.0.1 + token)
                                   │
   ┌───────────────────────────────────────────────────────────────┐
   │                   BACKEND DAEMON  (Python)                      │
   │  catalogue · installer(jobs) · tester · watchers · superviseur  │
   │  état système · bases SQLite · logs                             │
   └───────────────────────────────────────────────────────────────┘
```

**Conséquence directe** : la version/technologie de la GUI devient sans importance
pour le backend → on choisit la coquille adaptée à l'OS (Tauri v2 pour 24.04+,
Tauri v1 pour 20.04, navigateur système, etc.) sans jamais toucher au métier.

---

## 2. Séparation « affichage pur » vs « passerelle »

| Reste DANS la coquille GUI (jamais dans l'API) | Passe par l'API (tout le reste) |
|---|---|
| Taille / position de fenêtre (`LogicalSize`, `getCurrentWindow`) | Toute lecture/écriture de données |
| Splashscreen / fermeture fenêtre (`close_splashscreen`) | Tout appel système / install |
| Thème, layout, état d'affichage local | Tout accès base / catalogue / jobs |
| Rendu, animations | Tout état système / supervision |

Règle mnémotechnique : **« si ça survit à un changement de GUI, c'est dans l'API. »**

---

## 3. Inventaire complet des opérations « accessibles depuis l'externe »

Consolidation des 3 surfaces actuelles :
- **[T]** commande Tauri (`invoke_handler!`, `main.rs`)
- **[P]** sous-commande CLI de `gui_helper.py`
- **[H]** endpoint HTTP existant (`catalogue_server.py`)

Colonne « Impl. actuelle » = où vit réellement la logique aujourd'hui.

### A. Système & environnement
| Opération API (proposée) | Params | Retour | Sources actuelles | Impl. actuelle |
|---|---|---|---|---|
| `system.info` | — | `{os, arch, home}` | [T] `get_system_info`, [T] `get_platform` | Rust natif |
| `system.deps.check` | `config?` | liste `{name, installed, version, min}` | [T] `check_dependencies`, [T] `check_dependencies_with_config`, [T] `check_python_deps`, [P] `check_python_deps` | Rust + Python |
| `system.deps.install` | `name` | `{ok, log}` | [T] `install_dependency`, [P] `install_pip` | Rust (apt/brew) + Python (pip) |
| `system.state.get` | — | état système (CPU/RAM/…) | [T] `get_system_state`, [P] `get_system_state` | Python (`psutil`) |
| `system.state.save` | — | `{ok}` | [T] `save_system_state`, [P] `save_system_state` | Python |

### B. Bases de données
| Opération API | Params | Retour | Sources | Impl. |
|---|---|---|---|---|
| `db.init` | — | `{ok}` | [T] `init_databases`, [P] `init_databases` | Python (`sql.db`) |
| `db.check` | — | état des bases | [T] `check_databases`, [P] `check_databases` | Python |

### C. Catalogue
| Opération API | Params | Retour | Sources | Impl. |
|---|---|---|---|---|
| `catalogue.tools.list` | — | liste outils | [T] `get_catalogue_tools`, [P] `get_catalogue_tools`, [H] `GET /api/tools` | Python (`CatalogueDB`) |
| `catalogue.seed` | — | `{ok, count}` | [T] `seed_catalogue`, [P] `seed_catalogue` | Python |
| `catalogue.sync` | `url?` | `{ok, synced}` | [T] `sync_catalogue`, [P] `sync_catalogue_remote` | Python |
| `catalogue.tools_table.update` | — | `{ok}` | [P] `update_tools_table` | Python |

### D. Outils installés (opérations synchrones)
| Opération API | Params | Retour | Sources | Impl. |
|---|---|---|---|---|
| `tools.installed.list` | — | liste `{ref, status}` | [T] `get_installed_tools`, [P] `get_installed_tools` | Python |
| `tools.install` | `ref` | `{status, log}` | [T] `install_tool`, [P] `install_tool` | Python (recettes) |
| `tools.uninstall` | `ref` | `{status, log}` | [T] `uninstall_tool`, [P] `uninstall_tool` | Python (recettes) |

### E. File d'installation (jobs asynchrones, table `install_jobs`)
| Opération API | Params | Retour | Sources | Impl. |
|---|---|---|---|---|
| `jobs.add` | `ref, name, job_type` | `job_id` | [T] `install_queue_add` | Rust → SQLite (à porter Python) |
| `jobs.cancel` | `id` | `{ok}` | [T] `install_queue_cancel` | Rust (kill + update) |
| `jobs.list` | — | liste jobs | [T] `install_queue_status` | Rust → SQLite |
| `jobs.clear` | — | `{ok}` | [T] `install_queue_clear` | Rust → SQLite |

### F. Processus & services (supervision)
| Opération API | Params | Retour | Sources | Impl. |
|---|---|---|---|---|
| `processes.list` | — | snapshot processus | [T] `process_list` | Rust (proc-monitor) |
| `processes.log` | `id` | tail de log | [T] `process_log` | Rust |
| `services.list` | — | état des services | [T] `service_list` | Rust (registre) |

### G. Watchers (état vivant, poll)
| Opération API | Params | Retour | Sources | Impl. |
|---|---|---|---|---|
| `watch.get` | `name` (`installed-tools` \| `sys-state`) | cache JSON | [T] `watch_get` | Rust lit cache alimenté par services Python |

### H. Logs
| Opération API | Params | Retour | Sources | Impl. |
|---|---|---|---|---|
| `logs.read` | — | contenu log | [T] `read_debug_logs` | Rust (fichier) |
| `logs.write` | `level, message` | `{ok}` | [T] `log_message` | Rust |
| `health` | — | `{ok, version}` | [H] `GET /health` | Python |

### I. Services longue durée (démarrés par le superviseur, PAS des appels requête/réponse)
Ce sont des **processus**, pas des endpoints. Le daemon les supervise.
| Service | Source | Impl. |
|---|---|---|
| Serveur catalogue | [H] `catalogue_server.py` | Python HTTP |
| Worker installeur | [P] `run_installer_service` | Python (consomme `install_jobs`) |
| Testeur | [P] `run_tester_service` | Python |
| Watcher outils installés | [P] `watch_installed_tools` | Python |
| Watcher état système | [P] `watch_system_state` | Python |
| proc-monitor | (Rust) | à porter/garder |
| superviseur | (Rust) | à porter dans le daemon |

### J. À NE PAS exposer / à sécuriser (portes dérobées génériques)
| Élément | Risque | Décision proposée |
|---|---|---|
| [T] `run_command(command, args)` | exécution shell arbitraire via API locale | **supprimer** ou restreindre à une allow-list |
| [T] `run_python_script(path, args)` | exécution Python arbitraire | **supprimer** ou restreindre |
| [T] `close_splashscreen` | — | **reste GUI pure** (pas dans l'API) |

---

## 4. Le contrat d'API (proposition)

### Transport
- **HTTP/JSON sur `127.0.0.1:<port>`** (généralise le `catalogue_server` existant).
  Alternative : socket Unix (`~/.modelweaver/api.sock`) — plus sûr, mais moins
  universel pour un futur client navigateur.
- **Versionné** : préfixe `/v1/…`. Toute évolution incompatible ⇒ `/v2/…`.
- Nommage : `domaine.action` → route `/v1/<domaine>/<action>`
  (ex. `POST /v1/tools/install {"ref": "..."}`).

### Sécurité (non négociable)
1. Bind **strict** sur `127.0.0.1` (jamais `0.0.0.0` — le catalogue actuel bind
   `0.0.0.0`, **à corriger**).
2. **Token de session** : le daemon écrit `~/.modelweaver/api.token` (perms `600`)
   au démarrage ; tout client doit l'envoyer (`Authorization: Bearer <token>`).
   Empêche un onglet navigateur ou un process tiers d'appeler l'API.
3. Opérations « sensibles » (install/uninstall/deps) journalisées.

### Cycle de vie
- Le **bootstrap** (fin, portable) démarre le daemon puis ouvre la coquille GUI/CLI.
- Le **superviseur vit DANS le daemon** (aujourd'hui en Rust) → backend 100 %
  indépendant de Tauri.
- Arrêt propre : endpoint `POST /v1/system/shutdown` (protégé par token).

---

## 5. Le « SDK client » partagé

Un objet unique encapsule l'appel API, réutilisé par **toutes** les interfaces :

- **TypeScript** (`mwClient.ts`) pour les GUIs et le web :
  `mw.tools.install(ref)` → `POST /v1/tools/install`.
- **Python** (`mw_client.py`) pour le CLI et le testeur :
  mêmes méthodes, même contrat.

La GUI remplace ses `invoke(...)` par `mw.<domaine>.<action>(...)`.
Le CLI remplace ses appels directs à `gui_helper` par le même SDK.
→ **Aucune logique métier dans les interfaces.**

---

## 6. Chemin de migration (incrémental, sans big-bang)

1. **Créer le daemon** = généraliser `catalogue_server.py` en `mw_daemon.py`
   (routing, token, health) et y **importer** les fonctions déjà présentes dans
   `gui_helper.py` (elles existent quasi toutes).
2. **Porter les 4 ops `jobs.*` et proc/service** de Rust → Python (equivalents déjà
   là via le service installeur & watchers).
3. **Écrire le SDK** TS + Python.
4. **Router 2-3 commandes** GUI via le SDK (ex. `tools.installed.list`, `catalogue.tools.list`)
   pour valider le pattern de bout en bout.
5. **Basculer le reste** commande par commande ; la GUI Tauri devient coquille.
6. **Retirer** `run_command` / `run_python_script`, vider `invoke_handler!`.
7. **Le bootstrap** démarre le daemon ; le superviseur migre dans le daemon.
8. Dès lors : décliner les coquilles (Tauri v1 pour 20.04, web, etc.).

---

## 7. Bénéfices / bémols

**Bénéfices**
- Compat OS résolue à la racine (la GUI n'impose plus le plancher glibc/webkit).
- CLI et GUI strictement iso-fonctionnels (même API).
- Testabilité : le testeur `curl` l'API.
- Modes bonus quasi gratuits : navigateur, TUI, headless.

**Bémols (maîtrisés)**
- Sécurité de l'API locale à faire sérieusement (bind + token).
- Un contrat d'API à maintenir (versionné).
- Un poil plus de plomberie JSON qu'un `invoke` direct.
