# ModelWeaver

Orchestrateur IA cross-platform qui installe et coordonne Ollama, LiteLLM,
OpenCode et Open WebUI.

## Architecture

### V0.1 (Actuelle)
Deux phases :
- `modelweaver.sh` — bootstrap minimal en sh portable
- `modelweaver.py` — cœur Python (audit, cache, installation)

### V0.2 (Terminée ✅) — Le Grand Split en Modules
Architecture divisée en 3 couches et 9 modules interconnectés :

**Couche 1 : Données, Sécurité & Découverte Automatique**
1. **Le Catalogue** (`/catalogue`) : Source de vérité (JSON).
2. **Le Gestionnaire de Clés** (`/key_manager`) : Coffre-fort et onboarding automatique.

**Couche 2 : Le Moteur Logique (Le Core)**
3. **Le Checkeur** (`/checker`) : Inspection du système et génération de `system_state.json`.
4. **L'Installeur** (`/installer`) : Préparation d'environnement et gestion des outils.
5. **Le Gestionnaire de Conteneurs** (`/container_manager`) : Orchestration Docker pour exécution isolée.
6. **Le Module de Test** (`/test_runner`) : Validation des scripts agents dans Docker.
7. **Le Plombier** (`/plumber`) : Routeur d'API intelligent (fallback, quotas, adaptateurs).

**Couche 3 : L'Interface Utilisateur (UI)**
8. **L'Organiseur** (`/organiser`) : Studio de création visuel (Low-Code).
9. **Le Dashboard** (`/dashboard`) : Tour de contrôle (Play/Stop, logs, monitoring).

## Conventions

- Léger : stdlib Python uniquement, < 1 MB RAM
- Modes YES / NO / ASK (auto en non-interactif)
- Cache partagé dans `.modelweaver/cache/` (gitignoré)
- **Offline-first** : toujours vérifier le cache avant de télécharger. L'installation doit pouvoir se faire sans connexion internet si un cache prérempli est fourni.
- **Cache prérempli** (V0.2+) : prévoir des commandes pour préparer un cache d'installation complet (tous les binaires, archives, dépendances) en amont, utilisable ensuite hors-ligne.
- **Mise à jour du cache** (V0.2+) : quand internet est disponible, l'installeur pourrait vérifier si des versions plus récentes existent par rapport au cache, et les mettre à jour.
- Notation V0.2+ pour le pool d'idées (pioche au moment de définir les limites d'une version)
- Licence source-available : usage non-commercial libre, commercial interdit sans accord

## Composants

| ID | Type | Optionnel |
|---|---|---|
| ollama | binary | Oui (RAM < 8 Go → désactivé) |
| litellm | python-module | Non |
| opencode | binary | Non |
| open-webui | python-module | Oui |

## Règles de fonctionnement

### Commits

Après chaque requête qui modifie des fichiers, faire un commit local :
- Un seul commit par requête (toutes les modifs du lot ensemble)
- Message clair et concis
- Ne pas push tant que c'est pas demandé

### Budget temps

Avant chaque requête, donner une estimation courte (< 1 min si possible).
Si ça dépasse l'estimation :
1. Stopper net
2. Analyser pourquoi c'est long
3. Soit corriger le problème, soit accorder plus de temps
4. Jamais plus de 15 minutes en autonomie — au-delà, demander explicitement

Si aucune estimation n'est donnée, le premier point de contrôle est à 1 minute.

## État actuel

V0.1 terminée, V0.2 en cours de définition. Voir [VERSIONS.md](VERSIONS.md).

### Session récente (2026-07-05)
- Le bridge ModelWeaver est maintenant installé sous `opencode-modelweaver`.
- La commande `opencode` reste directe pour un usage standard.
- Les requêtes passent par LiteLLM avec un fallback visible et une trace de route dans `.modelweaver/route_trace.log`.
- Les erreurs d’authentification et de saturation déclenchent désormais un rebond explicite.
