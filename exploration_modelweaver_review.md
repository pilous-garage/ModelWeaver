# Exploration du repo ModelWeaver — synthèse & revue de la route `/health`

## 1. Vue globale du dépôt

### 1.1 Objectif
ModelWeaver est un backend Python qui centralise toute la logique métier d’un assistant/atelier d’outils/agents locaux. Toute interface (GUI Tauri, CLI, web/TUI) est prévue comme un simple client HTTP d’un même daemon local.

### 1.2 Architecture en couches
- **`services/api/`** : daemon HTTP unique, source de vérité externe.
  - `daemon.py` : serveur `ThreadingHTTPServer` bindé sur `127.0.0.1`, token Bearer, SSE, rate-limit, CORS.
  - `router.py` : routing statique `ROUTES` / `STREAMING_ROUTES` + routes dynamiques (`agents/{id}/...`, `team/...`).
  - `handlers/` : handlers métiers par domaine (`system`, `llm`, `agents`, `jobs`, `keys`, `chat`, `teams`, `monitoring`, `workspace`, `docker`).
  - `client.py` : SDK Python `MWClient` pour CLI/outils.
  - `_shared.py` : singletons lazy (`ModelWeaverDB`, `CatalogueDB`, `RuntimeDB`, `KeyManager`, `LLMManager`) + helpers.
  - `_contract/` : contrat public du service (`interface.py`, `dependencies.py`).
- **`modules/`** : briques de logique appelées en direct par le daemon/services.
  - `sql/` : couche données (`ModelWeaverDB`, `CatalogueDB`, `RuntimeDB`, `AgentsDB`, migrations, schémas SQL).
  - `checker/` : inventaire système/hardware/dependencies.
  - `key_manager/` : gestion sécurisée des clés API via keyring OS + onboarder `.env`.
  - `llm_manager/` : providers, modèles, bridges LLM, moteurs locaux.
  - `installer/` : recettes d’installation et jobs.
  - `telemetry/` : collecte de métriques.
  - `system/` : dépendances système.
  - `usage/` : collecte d’usage/budget.
- **`AgentFrameWork/`** : framework d’agents (FSM, dispatcher, router, pipeline, pause, stream bus, stockage agent, provisioning).
- **`AgentsCatalogue/`** : catalogue de rôles/comportements/skills/outils pour agents.
- **`services/`** : services longs supervisés (daemon API, installer worker, tester, watchers, supervisor, team manager, tarif, fs_auth, audit, ratelimit, logger).

### 1.3 Sécurité & contrat d’API
- Bind strict `127.0.0.1`, token Bearer stocké dans `~/.modelweaver/api.token` (`600`).
- Découverte clients via `api.port` + `api.token`.
- CORS dynamique pour webview Tauri.
- `hardcheck/` prévu pour vérifier exports/dépendances/contrats statiquement.

### 1.4 Bases de données
- `db.py` à la racine est un stub minimaliste.
- La vraie couche SQL vit dans `modules/sql/` (`sql_module.py`, `db.py`, schémas SQL, migrations).
- Singletons injectés dans le daemon via `services/api/_shared.py`.

## 2. Revue ciblée : route `/health` et dépendances DB

### 2.1 Constat
- La route **`/health`** existe **uniquement** en tant que GET spécial dans `MWAPIHandler.do_GET`.
- Elle est **servie avant** le préfixe `/{API_VERSION}/` et **avant** l’auth Bearer.
- Son handler est inline dans `daemon.py` et retourne :
  - `ok: bool`
  - `version`
  - `api`
  - **Aucune lecture de base de données**.
- Aucune route `/v1/health` n’est déclarée dans `ROUTES` ni dans `EXPOSES`.

### 2.2 Vérification des consignes
1. **La route `/v1/health` utilise-t-elle les modules de domaine refactorisés et non `db.py` directement ?**
   - Réponse : **Non applicable telle quelle**, car `/v1/health` n’existe pas actuellement.
   - Si l’on considère `/health` comme endpoint de santé, il n’utilise **pas** `db.py` ; il n’utilise **pas non plus** `modules/sql/`, il ne dépend que de constantes (`MW_VERSION`, `API_VERSION`).
2. **Format JSON**
   - Réponse : cohérent avec le reste du daemon (`_send(200, {...})`), payload objet JSON simple.
3. **Cas d’erreur “base indisponible → status degraded”**
   - Réponse : **Non implémenté** sur `/health`. Aujourd’hui `/health` ne sonde pas la disponibilité des bases.
   - Le endpoint le plus proche est `/v1/db/check` (dans `handlers/system.py`), qui inspecte l’existence des fichiers DB et peut renvoyer des erreurs par base.
4. **Utilisation de `db.py` racine**
   - Réponse : **Aucun usage** de `db.py` racine dans la route `/health`.

### 2.3 État des contrats
- `services/api/_contract/interface.py` ne déclare pas `/health`.
- `services/api/_contract/dependencies.py` ne liste pas de dépendance DB pour un endpoint `/health`.

## 3. Conclusion de la revue

| Point | État |
|---|---|
| Route `/v1/health` | Absente ; seul `/health` existe hors version et hors auth |
| Dépendance à `db.py` racine | Absente sur `/health` |
| Modules domaine utilisés | Aucun pour `/health` actuel |
| Format JSON | Conforme |
| Cas “degraded” base indisponible | Non couvert par `/health` ; couvert partiellement par `/v1/db/check` |
| Contrat `_contract` | Non documenté pour `/health` |

## 4. Pistes d’alignement (si souhaité)
- Ajouter une route `/v1/health` versionnée, authentifiée, cohérente avec `EXPOSES`.
- Faire dépendre sa réponse de `modules/sql/` plutôt que de `db.py` racine.
- Ajouter un statut `degraded` quand les bases sont inaccessibles, tout en gardant `/health` like “daemon vivant” séparé de `/v1/db/check`.
