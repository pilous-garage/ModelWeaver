# ModelWeaver

Orchestrateur IA cross-platform qui installe et coordonne Ollama, LiteLLM,
OpenCode et Open WebUI.

## Architecture

Deux phases :
- `modelweaver.sh` — bootstrap minimal en sh portable
- `modelweaver.py` — cœur Python (audit, cache, installation)

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
