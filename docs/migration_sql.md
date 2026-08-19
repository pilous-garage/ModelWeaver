# migration_sql.md — Conseils de réorganisation par domaines

> **Niveau : ANALYSIS / PROPOSITION (pas de migration exécutée).**
> Objectif : regrouper les tables actuelles dans des **domaines métiers
> cohérents, chacun avec UN writer dédié (un token)**. Les migrations
> concrètes seront validées puis appliquées séparément.

## 0. Frontière réelle : déjà migré vs imports vivants `modules.sql.*`

La refonte `modules/sql_old → modules/sqlite` est PARTIELLE. Vérifié le
2026-08-19 (audit automatisé) :

**`modules/sql` = shim de compat, PLUS QUE 2 fichiers** : `db.py` +
`__init__.py` réexportent depuis `modules.sqlite` (`Db`, `CatalogueDB`,
`RuntimeDB`, `ModelWeaverDB`). Tout sous-module `modules.sql.*` autre que
`db`/racine est **ABSENT** → `ImportError` au premier appel :

| sous-module importé | nb d'imports vivants | existe ? |
|---|---|---|
| `modules.sql.db` | 108 | ✔ (shim) |
| `modules.sql.workspace` | 52 | ❌ |
| `modules.sql.catalogue_repo` | 30 | ❌ |
| `modules.sql.agents_repo` | 25 | ❌ |
| `modules.sql.sql_module` | 20 | ❌ |
| `modules.sql.schema` | 15 | ❌ |
| `modules.sql.agent_repository` | 6 | ❌ |
| `modules.sql.catalogue_local` | 5 | ❌ |
| `modules.sql.catalogue_server` | 4 | ❌ |
| `modules.sql.migrations` | 3 | ❌ |
| `modules.sql.catalogue_genere` | 3 | ❌ |
| `modules.sql.runtime_repo` | 2 | ❌ |
| `modules.sql.orchestration_repository` | 2 | ❌ |
| `modules.sql.modelweaver_repo` | 2 | ❌ |

≈ **189 fichiers** importent `modules.sql.*` (incl. `modules/sql_old/*`,
`AgentsCatalogue/lib/*`, `services/*`, `modules/usage/*`, `modules/catalogue/*`,
`modules/key_manager/*`, `projetadmin/*`, `tests/*`, `scripts/*`).

Conséquences :
- **Déjà migré & vivant** dans `modules/sqlite/` : `local`, `buffer`,
  `workspace` (18 tables), `task` (TaskFlow), `agent`, `security`, `genere`
  + les NOUVEAUX domaines bridge : `info_llm`, `runtime_llm`, `keys`,
  `score`, `batch` (créés en session 2026-08-18).
- **Le vrai chantier** = recâblage des ~189 imports de `modules.sql.*` vers
  les domaines `modules.sqlite.*` correspondants (le shim ne couvre QUE
  `db`/racine ; tout le reste échoue au run) : score/batch/infra +
  workspace/task/agent/security.
- **Fusion workflow NON faite** : `task.db` (5 tables) et `workspace.db`
  (18 tables dont doublons `tasks`, `task_dependencies`, `ask_new_task`)
  sont migrés mais pas consolidés (doublons §1).
- **Domaine `infra` (runtime.db) : ABSENT** — aucun dossier
  `modules/sqlite/infra` ; `processes`/`services`/`install_jobs` nont pas
  de domaine dédié.
- **BRIDGE (info_llm + runtime_llm + keys) : FAIT** (compacteur + adress +
  KeyManager) — reste le câblage réel des readers côté bridge
  (`runtime_llm/agents` lisent encore le local v4 direct).

## 1. Constats (état des lieux)

| domaine actuel | db | writer | état |
|---|---|---|---|
| `local` | local_catalogue.db | `write_catalogue` (token OK) | OK — référentiel versionné multi-sources |
| `buffer` | buffer.db | `write_buffer` (token OK) | OK — file pending in/out |
| `task` | task.db | `write_task` | migré mais refonte en cours (TaskFlow V0.15) |
| `workspace` | workspace.db | **OUVERT** (pas de token) | ⚠️ writes concurrents supervisor+agents sur 18 tables (dont doublons task) |
| `agent` | agents.db | **`__init__.py` VIDE** — pas de `db/db_ro/get_writer`, câblage via `agent.py`/`AgentDomain` (ouvert, sans token) | ⚠️ tokeniser + exposer la surface |
| `security` | security.db | **`__init__.py` VIDE** — idem via `security.py`/`SecurityDomain` (1 table `agent_fs_auth`, ouvert) | ⚠️ tokeniser + exposer la surface |
| `genere` | catalogue_genere.db | ouvert, pas de token, pas de `db_ro/get_writer` | ⚠️ décision open/tokenisé |
| `runtime` | runtime.db | **raw sqlite3 (pas de layer `Db`)** | ⚠️ PAS migré — imports morts `modules.sql.*` (score/batch/infra) — PAS de domaine `infra` créé |
| `runtime_llm` | runtime.db (tables bridge) | **OUVERT sans token (délibéré §6b)** | OK — 13 tables §6b (adresse_runtime, model_call_log+archive, real_call_models, really_used_budget, budget_consumption, budget_final, agent_budget_allocation, adress_error_state, budget_error_state, model_efficacy, endpoint_model_usage) |
| `info_llm` | info_llm.db | `write_info_llm` (compacteur, token OK) + `scripts/regen_info_llm.py` | OK — 15 tables §6a (DSL `model_capability` au lieu de `model_capabilities` ; pas de `cost_key_tag`, fusionné dans `cost_final`) |
| `keys` | keys.db | KeyManager + `WRITE_KEYS_TOKEN` (keyring OS / Fernet) | OK — §6c |
| `score` | score.db | `write_score` (token OK) + `writer_dedie.py` | OK — 10 tables §3 (score_thinking_power, buckets, blocks, score_batch, benchmark) |
| `batch` | batch.db | `write_batch` (token OK) + `writer_dedie.py` | OK — 10 tables §3 (usage_history_{1m..1mo}, model_sequence, llm_caller_sessions, archive_processing_report) |

Points durs à valider :
- **Doublons majeurs** : `tasks`, `ask_new_task`, `task_dependencies` existent
  **à la fois** dans `task.db` (5 tables : task_meta, entry_request, tasks,
  task_dependencies, ask_new_task) **et** dans `workspace.db` (version legacy,
  + sub_tasks, sub_task_dependencies, task_supervisor_rules, task_log,
  task_budget_tracking, task_attachments, chatroom_messages, human_choice,
  question, reponse, usage_files, issues, workspace_config).
  → conflit de schéma, à consolider (fusion workflow + dialogue_agent).
- `workspace.db` mélange **gestion de projet** (workspaces, workspace_config,
  root_tasks) et **moteur de tâche**
  (tasks, sub_tasks, task_dependencies, ask_new_task, task_supervisor_rules,
  task_log, task_budget_tracking, task_attachments) + **dialogue**
  (chatroom_messages, human_choice, question, reponse, usage_files) +
  legacy mort (issues, workspace_config). — la table `provider_endpoint_api_key_type` n'est PAS dans
  workspace ; les annotations budgets sont dans info_llm.
- `score`/`batch` sont maintenant **créés et tokenisés** (write_score /
  write_batch, writer_dedie.py) mais **0 importeur** : les consommateurs
  passent encore par les imports morts `modules.sql.*` (le plus gros chantier
  = recâblage, PAS la création des tables).
- **`agent`/`security` : `__init__.py` VIDES** → surface `db/db_ro/
  get_writer` absente, pas de token (câblage ouvert via agent.py/security.py)
  → à exposer + tokeniser.
- **Domaine `infra` absent** : `runtime.db` (raw sqlite3) n'a pas de dossier
  `modules/sqlite/infra` ; `processes`/`services`/`install_jobs`/`meta` ne
  sont pas migrés (seules les tables score/batch/runtime_llm prévues §3/§6b
  le sont, dans leurs propres dbs).

## 2. Proposition de regrouplement (old → new)

Principe : **un domaine métier = une db + UN writer dédié (token)**.
Les migrations sont des *moves de tables* (même db tant qu'aucune fusion de
schéma n'intervient) ; la regrouper sémantiquement d'abord.

| # | domaine (nouveau) | db cible | writer (token) | propriétaire |
|---|---|---|---|---|
| 1 | `local` | local_catalogue.db | `write_catalogue` | référentiel (inchangé) ✅ FAIT |
| 2 | `buffer` | buffer.db | `write_buffer` | file tampon (inchangé) ✅ FAIT |
| 3 | `workflow` | **workflow.db** | `write_workflow` | moteur TaskFlow (fusion task+workspace tables-tâche) ❌ À FAIRE |
| 4 | `workspace` | **workspace.db** | `write_workspace` | gestion projet (pas le moteur) ⚠️ OUVERT — à tokeniser + désasocier du moteur |
| 5 | `score` | **score.db** | `write_score` | scoreur → *score-experience* (scores, efficacité) ✅ FAIT (10 tables, writer_dedie.py) — mais 0 importer |
| 6 | `batch` | **batch.db** | `write_batch` | batcheur (usage_history, sequences, modèle de réussite) ✅ FAIT (10 tables, writer_dedie.py) — mais 0 importer |
| 7 | `agent` | agents.db | `write_agent` | identité/runtime agents ⚠️ `__init__.py` VIDE, ouvert — à exposer + tokeniser |
| 8 | `security` | security.db | `write_security` | fs auth / agents ⚠️ `__init__.py` VIDE, ouvert — à exposer + tokeniser |
| 9 | `genere` | catalogue_genere.db | `write_genere` | tables gen_* ⚠️ ouvert, pas de db_ro/get_writer — décision open/tokenisé |
| 10 | `infra` | runtime.db | `write_infra` | processes/services/install_jobs (runtime infra) ❌ DOMAINE ABSENT — à créer |

> Domaine 11 (bridge, §6) : `info_llm` ✅ + `runtime_llm` ✅ (ouvert, délibéré
> §6b) + `keys` ✅ — tous FAITS en session 2026-08-18.

## 3. Correspondance old → new par TABLE

### Score (score-experience) → `score.db` / `write_score` ✅ FAIT
Provenant de `runtime.db` (modules/sql_old/runtime_repo.py + modules/usage/*) :
`score_thinking_power`, `model_bucket_counts`, `score_bucket_heads`,
`score_batch_blocks_{5m,1h,1d}`, `score_batch`, `score_benchmark_etire`,
`score_benchmark_meta`, `meta`.
→ Répartition réelle (session 2026-08-18) : ⚠️ `really_used_budget`,
`endpoint_model_usage`, `budget_consumption`, `model_efficacy` (ex
`local_model_efficacy`), `real_call_models` ont été placés dans
**`runtime_llm`** (domaine bridge §6b, écriture par appel) ; ⚠️
`llm_caller_sessions`, `archive_processing_report`, `usage_history_*`,
`model_sequence` sont dans **`batch`**. `agent_actif` n'a pas de table
dédiée dans les schémas sqlite (usage runtime ?). À CONFIRMER si la
répartition est acceptée vs le renommage conseillé (`experience_*`).
→ Le domaine `score` est tokenisé + `writer_dedie.py` ✅ mais **0 importer**
(consumers encore sur `modules.sql.*`).

### Batcheur (batch + sequences) → `batch.db` / `write_batch` ✅ FAIT
`usage_history_{1m,15m,3h,1d,1w,1mo}`, `model_sequence`, **sequences**
(`_rebuild_sequences` / `score_batches`), `llm_caller_sessions`,
`archive_processing_report`, `meta`.
→ Le batcheur est le SEUL writer de `batch.db` ; les agents runtime écrivent
leur détail dans `score.db`/`runtime_llm.db` et le batcheur agrège.
→ ⚠️ `score_batch`/`score_batch_blocks_*` (agrégats) sont restés dans
`score` et non dans `batch` — à valider (la spec §3 les plaçait côté
batcheur). Tokenisé + `writer_dedie.py` ✅, 0 importer.

### Workflow (fusion task+workspace) → `workflow.db` / `write_workflow` ❌ À FAIRE
Depuis `task.db` + tables-tâche de `workspace.db` :
`entry_request`, `tasks` (**UNE** table, supprimer la double),
`sub_tasks`, `sub_task_dependencies`, `task_dependencies` (**UNIFIQUÉ**),
`ask_new_task`, `task_supervisor_rules`, `task_log`,
`task_budget_tracking`, `task_attachments`. **Nouvelles tables du domaine**
(décis. 2026-08-19) : `project` (data-type (source, project_ref), user_dir) +
`local_git_repo` (registre des repos, état de sync distant) — les refs
repo/commit des tasks s'appuient dessus.
→ Réalité actuelle : `task.db` ne contient que `task_meta`, `entry_request`,
`tasks`, `task_dependencies`, `ask_new_task`, `task_attachments`,
`project`, `local_git_repo` (8) ;
`sub_tasks`,
`sub_task_dependencies`, `task_supervisor_rules`, `task_log`,
`task_budget_tracking` + les doublons vivent dans `workspace.db`.
→ Le scoreur lit `task_log`/`task_budget_tracking` en RO (consumer).
→ **Action clef** : supprimer la copie workspace.db de `tasks`,
`ask_new_task`, `task_dependencies` une fois `task.db` adopté.

**Mapping décidé (table → table)** — décisions 2026-08-19 :

| Table workspace (aujourd'hui) | Cible (task.db) | État |
|---|---|---|
| `root_tasks` | → `entry_request` (fusion, racine immuable) | ✅ schéma task |
| `tasks` | → `tasks` (refonte `parent_task_id`, `entry_request_id`, `sub_task_type`) | ✅ schéma task |
| `sub_tasks` | → fusion dans `tasks` | ✅ schéma task |
| `sub_task_dependencies` | → fusion dans `task_dependencies` | ✅ schéma task |
| `task_dependencies` | → `task_dependencies` (unifiée) | ✅ schéma task |
| `ask_new_task` | → `ask_new_task` | ✅ schéma task |
| `task_supervisor_rules` | → `task_supervisor_rules` | ❌ à ajouter (règles par team → superviseur) |
| `task_reports` **+** `task_files` | → **`task_attachments`** (fusion V2 : annexes hors git, `kind` text/file) | ✅ modèles sqlite (workspace + task) ; migration V2 en `__init__.py` (copie + drop) |
| `task_log` | → `task_log` (lu par le scoreur en RO) | ❌ à ajouter |
| `task_budget_tracking` | → `task_budget_tracking` | ❌ à ajouter |
| `chatroom_messages` | → **`conversation` + `message`** (dialogue_agent, arborescent, fork de fils) | ✅ FAIT |
| `human_choice` | → `human_choice` (dialogue_agent ; issue_id retiré, + task_id/sub_task_id) | ✅ FAIT |
| `question`, `reponse` | → `question`/`reponse` (dialogue_agent, consensus) | ✅ FAIT |
| *(nouveau)* `humain_as_agent` | → dialogue_agent (agent_id NÉGATIF = point de terminaison humain) | ✅ FAIT |
| *(nouveau)* `direct_message` + `archive_direct_message` | → dialogue_agent (1:1 courts, kind request/reply/broadcast, interruption none/entrypoint, flush_tick) | ✅ FAIT |
| `issues` | → **DELETE** (code legacy : waker issue_open, _complete_done_issues, create_issue, issue_block, human_choice.issue_id) | décidé 2026-08-19 |
| `workspace_config` | → **DELETE** (0 ligne, aucun caller) | décidé 2026-08-19 |
| `usage_files` | → **DELETE** (jamais câblé, aucun caller) | décidé 2026-08-19 |
| `workspaces` | → **DELETE** (project_id porté par la team spec/manifest) | décidé 2026-08-19 |
| *(nouveau)* `project` + `local_git_repo` | → `task.db` : data-type (source, project_ref) — registre des repos, refs repo/commit des tasks, état pull distant, user_dir (vue utilisateur) | décidé 2026-08-19 |

### Workspace (projet) → **DISPARAÎT** (décidé 2026-08-19)
`workspaces`/`workspace_id` sont remplacés par `project_id` (porté par la team
spec/manifest, ex. `llm-code.team.yaml: project_id: mw-swarm`). Toutes les
tables de `workspace.db` partent :
→ moteur de tâche → `task.db` ; dialogue → `dialogue_agent.db` ;
`issues`, `workspace_config`, `usage_files`, `workspaces` → **supprimées**.
⚠️ Recâblage : `agent_manager` résolvait `project_id` via `workspaces.director`
(agent_manager/service.py:1697-1711) → à remplacer par le project_id du
manifest/team.

### Dialogue agent → `dialogue_agent.db` ✅ FAIT (domaine OUVERT, pas de
writer dédié ni de token — write.py/read.py exposent les fonctions ; si
spam/attaque → on ajoutera un writer qui filtre les signaux)
- `humain_as_agent` : points de terminaison humains (**agent_id NÉGATIF** —
  un humain est un agent ; plusieurs humains/points possibles).
- `conversation` + `message` : chatroom **arborescent** (parent_conv_id =
  fork de fils), par project_id/team_id ; dialogues humain↔agent dans la
  MÊME arborescence (agent négatif) ; messages : msg_type, content,
  attachments (JSON de paths génériques), pas d'UNIQUE.
- `direct_message` : échanges 1:1 courts (kind request/reply/info/broadcast,
  interruption none/entrypoint — FSM gérera l'entrypoint plus tard),
  `received`, flushable via **`flush_tick`** (flush.py : limit_lines,
  tick_duration_s, eligible_sql, archive → `archive_<table>` + rapport
  d'anomalies pour étude LLM ; jamais de flush de non-reçus/récents).
  `send_broadcast` = 1 ligne par membre. `interruption` prête pour le futur.
- `human_choice` : escalade humain (issue_id RETIRÉ — issues disparaît ;
  + task_id/sub_task_id).
- `question`/`reponse` : consensus agent→agent (skills + greedy-consensus) —
  idem, + agent négatif possible.
→ Décidé 2026-08-19 ; migré depuis `workspace.db` (§5) : chatroom_messages →
conversation+message (project_id via director, fallback workspace_id).

### Agent → `agents.db` / `write_agent` (tokeniser) ⚠️
`meta`, `agents`, `agent_runtime`, `agent_metrics`, `agent_signals`,
`agent_entrypoints`, `auth_requests`.
→ Schéma présent (7 tables) ✅ mais `__init__.py` VIDE (câblage via
`agent.py`/`AgentDomain`, ouvert) — à exposer `db/db_ro/get_writer` + token.

### Security → `security.db` / `write_security` (tokeniser) ⚠️
`agent_fs_auth` (+ schema security.py).
→ 1 table ✅ mais `__init__.py` VIDE (câblage via `security.py`/
`SecurityDomain`, ouvert) — à exposer + tokeniser.

### Infra (runtime) → `runtime.db` / `write_infra` ❌ À CRÉER
`processes`, `services`, `install_jobs`, `meta`.
→ Aucun dossier `modules/sqlite/infra` ; ces tables restent en raw sqlite3
dans runtime.db.

## 4. Risques / points à valider (check-list)

1. **Fusion `tasks`** — deux tables `tasks` (+`ask_new_task`,+
   `task_dependencies`) : valider d'abord le schéma unifié
   `modules/sqlite/task/task_schema.sql` (entrypoint =
   `entry_request`, arbre auto-référentiel `tasks.parent_task_id`,
   `sub_task_type`), puis purger les copies workspace. → PRÉALABLE : le
   `buffer_op` consumer et `local/catalogue` ne touchent pas ces tables, OK.
   NB : les deux tables sont DÉJÀ migrées (modules/sqlite) — il ne reste que
   la consolidation, pas une migration.
2. **Workspace OUVERT** : passer `write_workspace` + migrer le supervisor hors
   workspace.db → risque de lock concurrent ; à faire après le découpage
   `workflow`/`workspace`.
3. **Score/batch = LE chantier principal** : `modules/usage/*` et autres
   importent `modules.sql.db.RuntimeDB` (package SUPPRIMÉ — échec au premier
   appel). Sortir les tables runtime.db vers `score.db`/`batch.db` + layer
   `Db`/token, recâbler les 9 imports morts (§0). À faire AVANT la fusion
   workflow : rien dans le workflow ne dépend de usage/* (seul le batcheur
   lit `task_log` en RO — lecture tolérante OK pendant la transition).
4. **Tokeniser `agent`/`security`** : léger (ajouter les tokens dans
   `__init__`), à valider ensemble du batch score.
5. **`genere`** : aucun writer token → décider open ou tokenisé.

## 5. Ordre conseillé (par dépendance)

0. **bridge = info_llm + runtime_llm + vault** (nouveau, §6) — le prochain
   chantier : seule la donnée du bridge manque pour construire l'allocation
   pure.
1. **workflow** (fusion task.db + tables-tâche workspace.db, token
   `write_workflow`).
2. **workspace** (token `write_workspace` + découplage du supervisor).
3. **agent** / **security** (tokenisation) + **genere** (décision open).
4. **score/batch** (recâbler `modules.usage.*` + `modules.catalogue.*` sur
   les domaines créés).
5. **infra** (processes/services/install_jobs, depuis runtime.db résiduel).

`local`/`buffer` : stables, inchangés.

## 6. Domaine BRIDGE — info_llm / runtime_llm / vault (PROPOSITION)

Le bridge (llm_manager/direct_bridge + llm_allocation/allocate) est SEUL à
écrire dans ces domaines — PAS un service writer dédié : il existe dans
chaque thread agent, donc chaque thread détient le token et écrit en
per-ligne (WAL, jamais bloquant).

### 6a. `info_llm` (data STABLES — régénérées par le COMPACTEUR)

info_llm n'est PAS une copie des tables v3 : c'est une PROJECTION du local
catalogue v4, régénérée par le **compacteur** (§6e). Le bridge ne lit JAMAIS
local_catalogue directement — seulement ces tables, avec les ids « comme on
les aime » (ints stables par réf, upsert idempotent).

| info_llm table | provenance (local v4) | contenu compacté |
|---|---|---|
| `catalogue_providers` | `provider` | ref, api_type, doc/npm/env |
| `provider_endpoints` | `endpoint` | ref, url, api_type |
| `catalogue_models` | `model_official` | model_key, ref, description, tags familles |
| `model_capabilities` | `model_official` value/tags | context, max_output, function_calling, vision, tools, pricing… |
| `provider_models` | `model_provider_endpoint` | model chez provider (name, limits, free_tier, prix…) |
| `provider_models_mapping` | dérivé | alias `model_key` → provider_models |
| `provider_model_address` | providers × endpoints (+ typekeys) | l'IDENTITÉ des adresses — compléments manuels (api_key_tag, endpoints réels) marqués comme tels |
| `cost_key_tag` / `cost_final` | `model_provider_endpoint_typekey` | tarifs par tag de clé (typekey free/plus/premium…) |
| `alias_model` | manuel (annotations) | les alias non dérivables restent saisis à la main |
| `budgets` / `budget_tags` / `budget_user_key_id` / `budget_generique_key_tag` | **manuel** (config utilisateur) | DÉFINITIONS — le local v4 ne les contient pas |

Writer : `write_info_llm` — **détenu exclusivement par le compacteur**
(c'est ici un vrai service writer dédié : UN process régénère, pas les
threads). Le bridge est en lecture seule.

### 6e. Le COMPACTEUR (local_catalogue → info_llm)

- **Entrée** : local_catalogue v4 en mode ro (sélecteurs par source
  `models.dev`, tags).
- **Sortie** : les tables info_llm, toutes régénérées en UNE passe
  idempotente (upsert par réf) — les ints `id` existants sont CONSERVÉS
  (stable pour les joins du bridge), les nouvelles réfs prennent les ids
  suivants. Chaque ligne compactée porte `local_data_id` (le data_id v4)
  pour tracer la provenance.
- **Écriture** : via `write_info_llm` (batch, jamais d'upsert individuel).
- **Déclencheurs** : après un import models.dev (domaine local) ; route
  daemon `catalogue_local/compact` à la demande. Optionnel : re-compact
  périodique si annotations manuelles.
- **Invariant** : info_llm est une PROJECTION pure — toute édition manuelle
  doit être une colonne/annotation explicite (jamais sur les champs
  compactés, qui seront écrasés à la prochaine passe).
- Les tables v3 équivalentes (catalogue_models, provider_models… dans le
  vieux catalogue.db) deviennent OBSOLÈTES pour le bridge : le v3 n'est pas
  migré, il est retiré une fois le bridge branché sur info_llm.

### 6b. `runtime_llm` (data VOLATILES — écriture par ligne, per call)

| table (v3) | rôle |
|---|---|
| `adresse_runtime` | ÉTAT runtime par (adresse, clé) : available, backoff_until, error_since, first/last_use, first/last_respond — écrit à CHAQUE appel |
| `model_call_log` (+ `model_call_log_archive`) | log_calls_basiques : UNE ligne PAR appel (les colonnes "pauvres" ; le détail riche reste modèle side) |
| `really_used_budget` | budgets RÉELLEMENT consommés (le guess_work sera séparé) |
| `budget_consumption` | consommation par (budget, cible) à chaque appel |
| `budget_final` | quota COURANT calculé (reset périodique → updated par write) |
| `agent_budget_allocation` | budgets ALLOUÉS aux agents (allocation : écriture par allocation) |
| `adress_error_state` / `budget_error_state` | états d'erreur (rafale) — écrits par le bridge |
| `model_efficacy` | résultats réels par modèle (feuille brute de l'expérience — les SCORES dérivés iront au domaine score) |
| `endpoint_model_usage` | usage par (endpoint, modèle) par appel |

Writer : `write_runtime_llm` (détenu par les threads bridge) — écritures
par ligne, purge/archive par le batcheur (domaine batch) en lecture.

### 6c. `vault` (clés — protégé par keyring OS)

| table | rôle |
|---|---|
| `api_keys` (v3) + entrées keyring OS (key_manager : `keyring.set_password`) | key_value JAMAIS en clair sur disque ; `key_display` (ab****cd) et métadonnées (locked, tag free/paid, grade, health_status, expiration, last_tested_at…) en table |

C'est la SEULE donnée manquante pour construire le bridge : vault d'abord,
puis info_llm (adresses/capacités), puis runtime_llm (état/log).

### 6d. Exclus de bridge (à venir)
- **guess_work** (estimation) : `llm_task_type_score`, `scoring_task_types`,
  `scoring_domaines`, `llm_domaine_score`, `llm_effort_ratio`,
  `thinking_power_model/adress`, `model_provider_scoring`, `llm_task_cost`,
  `task_level_cost/stats` → futur domaine `guess`, PAS dans runtime_llm.
- **batch** : `usage_history_*`, `score_batch*`, `score_bucket*`,
  `score_benchmark_*`, `model_sequence`, `model_call_log_archive`
  (agrégations) → domaine batch (§3). Le bridge LIT `score_batch`/
  `score_benchmark_etire` pour l'allocation, n'y écrit pas.

---
*À valider domaine par domaine avant tout mouvement de tables.*
