# migration_sql.md — Cartographie `modules/sql` (legacy V1/V2) → `modules/sqlite` (V3/V4)

> **Stratégie "break-everything" (validé 2026-08-18) :** on laisse les appelants
> legacy (`flow_engine`, `file_watcher`, `taskflow_petri`, …) cassés tant que le
> domaine `sqlite` correspondant n’est pas complet. Rien n’est "branché"
> itérativement : **on ne répare / ne relance les bridges qu’une fois l’ensemble
> des domaines migrés.** Inversions de dépendances acceptées (modules.sql mort
> = `ModuleNotFoundError` volontaire) → corrigés en lot en fin de migration.
>
> Source : déduit des fichiers `modules/sql_old/*.sql` + `*.py` inline (OLD)
> vs `modules/sqlite/*/` (NEW). Une table peut apparaître dans plusieurs
> domaines OLD (duplication historique).

Légende :
- `→` migrée : nouvelle table de destination
- `renommée` : nouvelle table avec nom différent
- `fusionnée` : le contenu est absorbé par une autre table
- `supprimée` : table abandonnée volontairement
- `non migrée` : pas encore de table NEW (migration en cours ou hors-sqlite)

---

## 1. Domaines OLD (`modules/sql_old/`)

### 1.1 `agents_schema.sql` — Runtime agents

| table OLD | sort |
|---|---|
| `agents` | → `sqlite/agent/agents` |
| `agent_entrypoints` | → `sqlite/agent/agent_entrypoints` |
| `agent_metrics` | → `sqlite/agent/agent_metrics` |
| `agent_runtime` | → `sqlite/agent/agent_runtime` |
| `agent_signals` | → `sqlite/agent/agent_signals` |
| `meta` | → `sqlite/agent/meta` |

### 1.2 `catalogue_genere_schema.sql` — Cache calculé (symbols/pétri/export)

| table OLD | sort |
|---|---|
| `gen_data` | → `sqlite/genere/gen_data` (V4 : id_data int, path_data, last_modify) |
| `gen_dependance` | → `sqlite/genere/gen_dependance` |
| `gen_runs` | → `sqlite/genere/gen_runs` |
| `gen_fichier` | → `sqlite/genere/gen_fichier` |
| `gen_config` | → `sqlite/genere/gen_config` |
| `gen_file_rules` | → `sqlite/genere/gen_file_rules` |
| `gen_runtime_files` | → `sqlite/genere/gen_runtime_files` |
| `questions` | → `sqlite/genere/questions` |
| `meta` | → `sqlite/genere/meta` |

### 1.3 `catalogue_schema.sql` — Catalogue LLM (référence + budgets + scoring)

**Domaine NON encore migré** — décision 2026-08-18 : simplifié en 7 data_types
dans `sqlite/local` (voir `docs/catalogue_llm_spec.md`). Le tableau historique
ci-dessous garde les cibles d'origine ; le mapping actualisé est après le
tableau.

| table OLD | sort |
|---|---|
| `catalogue_providers` | non migrée → cible `sqlite/catalogue/catalogue_providers` |
| `provider_endpoints` | non migrée → cible `sqlite/catalogue/provider_endpoints` |
| `catalogue_models` | non migrée → cible `sqlite/catalogue/catalogue_models` |
| `provider_models` | non migrée → cible `sqlite/catalogue/provider_models` |
| `provider_models_mapping` | non migrée → cible `sqlite/catalogue/provider_models_mapping` |
| `provider_model_address` | non migrée → cible `sqlite/catalogue/provider_model_address` |
| `adresse_runtime` | non migrée → cible `sqlite/catalogue/adresse_runtime` |
| `alias_model` | non migrée → cible `sqlite/catalogue/alias_model` |
| `catalogue_aliases` | non migrée → cible `sqlite/catalogue/catalogue_aliases` |
| `catalogue_versions` | non migrée → cible `sqlite/catalogue/catalogue_versions` |
| `catalogue_outils` | non migrée → cible `sqlite/catalogue/catalogue_outils` |
| `classes_outils` | non migrée → cible `sqlite/local` (enum léger) ⚠ |
| `catalogue_recettes` | non migrée → cible `sqlite/catalogue/catalogue_recettes` |
| `catalogue_commands` | non migrée → cible `sqlite/local` (enum) ⚠ |
| `outils_popularite` | non migrée → cible `sqlite/catalogue/outils_popularite` |
| `model_benchmarks_raw` | non migrée → cible `sqlite/catalogue/model_benchmarks_raw` |
| `model_efficacy` | non migrée → cible `sqlite/catalogue/model_efficacy` |
| `model_provider_scoring` | non migrée → cible `sqlite/catalogue/model_provider_scoring` |
| `scoring_domaines` | non migrée → cible `sqlite/local` (enum) ⚠ |
| `scoring_task_types` | non migrée → cible `sqlite/local` (enum) ⚠ |
| `llm_domaine_score` | non migrée → cible `sqlite/catalogue/llm_domaine_score` |
| `llm_task_type_score` | non migrée → cible `sqlite/catalogue/llm_task_type_score` |
| `budgets` | non migrée → cible `sqlite/catalogue/budgets` |
| `budget_tags` | non migrée → cible `sqlite/local` (enum) ⚠ |
| `budget_generique_key_tag` | non migrée → cible `sqlite/catalogue/budget_generique_key_tag` |
| `budget_user_key_id` | non migrée → cible `sqlite/catalogue/budget_user_key_id` |
| `budget_final` | non migrée → cible `sqlite/catalogue/budget_final` |
| `cost_key_tag` | non migrée → cible `sqlite/catalogue/cost_key_tag` |
| `cost_final` | non migrée → cible `sqlite/catalogue/cost_final` |
| `agent_budget_allocation` | non migrée → cible `sqlite/runtime` ⚠ |
| `adress_error_state` | non migrée → cible `sqlite/runtime` (backoff) |
| `budget_error_state` | non migrée → cible `sqlite/runtime` (backoff) |
| `context_audit_log` | non migrée → cible `sqlite/runtime` (log) |
| `domain_writers` | non migrée → cible `sqlite/catalogue/domain_writers` ⚠ |
| `task_level_cost` | non migrée → cible `sqlite/runtime` (scoring) |
| `task_level_stats` | non migrée → cible `sqlite/runtime` (scoring) |
| `llm_effort_ratio` | non migrée → cible `sqlite/runtime` (scoring) ⚠ |
| `llm_task_cost` | non migrée → cible `sqlite/runtime` (scoring) ⚠ |
| `thinking_power_adress` | non migrée → cible `sqlite/runtime` (scoring) ⚠ |
| `thinking_power_model` | non migrée → cible `sqlite/runtime` (scoring) ⚠ |

> **Décision catalogue (2026-08-18)** : le catalogue LLM est **simplifié en 7
> data_types** dans `sqlite/local` (voir `docs/catalogue_llm_spec.md`) — plus de
> `sqlite/catalogue` de référence. Le tableau 1.3 ci-dessus est donc obsolète ;
> le mapping des tables OLD vers les 7 data_types :

| table OLD | nouvelle forme (data_type local_catalogue v4) |
|---|---|
| `catalogue_providers` | fusionnée → `provider` |
| `provider_endpoints` | fusionnée → `endpoint` + `provider_endpoint` |
| `catalogue_models` | fusionnée → `model_official` |
| `provider_models` | fusionnée → `model_provider_endpoint` (+ limites provider, status sourcé) |
| `provider_models_mapping` | fusionnée → `model_provider_endpoint` (partie déclarative) + domaine `adresse_llm` (declared/available) |
| `provider_model_address`, `adresse_runtime` | → domaine **`adresse_llm`** (séparé, runtime) |
| `alias_model` | **supprimée** — réconciliation = colonne `model_official_id` sur `model_provider_endpoint` |
| `catalogue_aliases` | → block outils (plus tard) |
| `budgets`, `budget_*`, `cost_key_tag`, `cost_final` | → domaine **budgets** (propre, consommations) |
| `agent_budget_allocation` | → domaine **budgets** |
| `adress_error_state`, `budget_error_state` | → domaine **adresse_llm** / budgets |
| `scoring_*`, `llm_*_score`, `llm_effort_ratio`, `llm_task_cost`, `task_level_*`, `thinking_power_*` | → domaine **scoring** (propre, écrivain dédié) |
| `model_benchmarks_raw`, `model_efficacy` | → data_type **`benchmark_model`** (plus tard, scrapers) |
| `context_audit_log` | → runtime log |
| `domain_writers` | → registre (local) |
| `classes_outils`, `catalogue_outils`, `catalogue_versions`, `catalogue_recettes`, `outils_popularite`, `catalogue_commands` | → **block outils** (block 2) |

### 1.4 `local_catalogue_schema.sql` — Catalogue local (méta/namespace/security)

| table OLD | sort |
|---|---|
| `global_local_data_type` | → `sqlite/local/global_local_data_type` |
| `global_local_meta` | → `sqlite/local/global_local_meta` |
| `global_local_namespace` | → `sqlite/local/global_local_namespace` |
| `global_local_path` | → `sqlite/local/global_local_path` |
| `global_local_privilege` | → `sqlite/local/global_local_privilege` |
| `global_local_privilege_condition` | → `sqlite/local/global_local_privilege_condition` |
| `global_local_security_supervisor` | → `sqlite/local/global_local_security_supervisor` |
| `global_local_shared_default` | → `sqlite/local/global_local_shared_default` |
| `global_local_buffer_op` | renommée → `sqlite/buffer/buffer_op` |

### 1.5 `modelweaver_schema.sql` — Monolithe V1 (runtime/service/install)

| table OLD | sort |
|---|---|
| `agents` | → `sqlite/agent/agents` |
| `chatroom_messages` | → `sqlite/workspace/chatroom_messages` |
| `classes_outils` | non migrée (doublon catalogue_schema) |
| `provider_models` | non migrée (doublon catalogue_schema) |
| `api_keys` | non migrée → cible `sqlite/security` (refonte auth) |
| `models`, `providers`, `model_providers` | non migrée → `modules/catalogue` |
| `agent_actif` | non migrée → runtime agent |
| `agent_connections` | non migrée → runtime agent |
| `agent_messages` | non migrée → runtime agent |
| `agent_queue` | non migrée → runtime agent |
| `budget_consumption` | non migrée → runtime usage |
| `really_used_budget` | non migrée → runtime usage |
| `endpoint_model_usage` | non migrée → runtime usage |
| `real_call_models` | non migrée → runtime usage |
| `local_installs` | non migrée → runtime install |
| `local_llms` | non migrée → runtime install |
| `local_outils` | non migrée → runtime install |
| `local_versions` | non migrée → runtime install |
| `local_model_efficacy` | non migrée → runtime install |
| `package_managers` | non migrée → runtime install |
| `commands` | non migrée → runtime service |
| `sessions` | non migrée → runtime service |
| `system_state` | non migrée → runtime service |
| `watchers` | non migrée → runtime service |
| `scheduled_jobs` | non migrée → runtime service |
| `service_ticks` | non migrée → runtime service |
| `service_tick_runs` | non migrée → runtime service |
| `service_ticks_secondes` | non migrée → runtime service |
| `tool_usage` | non migrée → runtime usage |
| `wakeup_calls` | non migrée → runtime agent_manager |
| `shared_tasks` | **supprimée** — remplacée par `sub_tasks` (taskflow V3) |

### 1.6 `workspace_schema.sql` — Domaine taskflow

| table OLD | sort |
|---|---|
| `workspaces` | → `sqlite/workspace/workspaces` |
| `workspace_config` | → `sqlite/workspace/workspace_config` |
| `root_tasks` | → `sqlite/workspace/root_tasks` |
| `tasks` | → `sqlite/task/tasks` + `sqlite/workspace/tasks` (partagée) |
| `sub_tasks` | → `sqlite/workspace/sub_tasks` |
| `sub_task_dependencies` | → `sqlite/workspace/sub_task_dependencies` |
| `ask_new_task` | → `sqlite/task/ask_new_task` + `sqlite/workspace/ask_new_task` (partagée) |
| `task_dependencies` | → `sqlite/task/task_dependencies` + `sqlite/workspace/task_dependencies` (partagée) |
| `task_supervisor_rules` | → `sqlite/workspace/task_supervisor_rules` |
| `task_files` | → `sqlite/workspace/task_files` |
| `task_log` | → `sqlite/workspace/task_log` |
| `task_budget_tracking` | → `sqlite/workspace/task_budget_tracking` |
| `human_choice` | → `sqlite/workspace/human_choice` |
| `chatroom_messages` | → `sqlite/workspace/chatroom_messages` |
| `question` | → `sqlite/workspace/question` |
| `reponse` | → `sqlite/workspace/reponse` |
| `issues` | → `sqlite/workspace/issues` |
| `usage_files` | → `sqlite/workspace/usage_files` |

### 1.7 `workspace.py` (inline) — Extensions runtime du taskflow

| table OLD | sort |
|---|---|
| `human_choice` | → `sqlite/workspace/human_choice` |
| `sub_task_dependencies` | → `sqlite/workspace/sub_task_dependencies` |
| `task_dependencies` | → `sqlite/task/task_dependencies` + `sqlite/workspace/task_dependencies` |
| `task_reports` | **supprimée** — fusionnée dans `task_log` ⚠ |

### 1.8 `catalogue_repo.py` (inline) — Tables additionnelles catalogue/runtime

Toutes **non migrées** (doublons du `catalogue_schema.sql` + log/probe runtime) :

| table OLD | sort |
|---|---|
| `capacite_log` | non migrée → runtime probe/log |
| `model_call_log` | non migrée → runtime probe/log |
| `model_call_log_archive` | non migrée → runtime probe/log |
| `model_capabilities` | non migrée → runtime probe/log |
| `model_endpoint_provider_capacite` | non migrée → runtime probe/log |
| `model_probe_state` | non migrée → runtime probe/log |
| `system_state` | non migrée → runtime service |
| `tool_usage` | non migrée → runtime usage |

(les tables communes avec `catalogue_schema.sql` — `adress_error_state`,
`adresse_runtime`, `budgets`, … — sont listées en 1.3)

### 1.9 `runtime_repo.py` (inline) — Runtime usage/scoring

Toutes **non migrées** :

| table OLD | sort |
|---|---|
| `agent_actif` | non migrée → runtime agent |
| `archive_processing_report` | non migrée → runtime |
| `budget_consumption` | non migrée → runtime usage |
| `endpoint_model_usage` | non migrée → runtime usage |
| `install_jobs` | non migrée → runtime install |
| `llm_caller_sessions` | non migrée → runtime usage |
| `local_model_efficacy` | non migrée → runtime install |
| `meta` | non migrée (doublon agents/genere) |
| `model_bucket_counts` | non migrée → runtime scoring |
| `model_success_runs` | non migrée → runtime scoring |
| `processes` | non migrée → runtime service |
| `real_call_models` | non migrée → runtime usage |
| `really_used_budget` | non migrée → runtime usage |
| `score_batch` | non migrée → runtime scoring |
| `score_batch_blocks_` | non migrée → runtime scoring |
| `score_benchmark_etire` | non migrée → runtime scoring |
| `score_benchmark_meta` | non migrée → runtime scoring |
| `score_bucket_heads` | non migrée → runtime scoring |
| `score_thinking_power` | non migrée → runtime scoring |
| `services` | non migrée → runtime service |
| `usage_history_` | non migrée → runtime usage |

### 1.10 `agents_repo.py` (inline) — Conversations / auth

| table OLD | sort |
|---|---|
| `auth_requests` | → `sqlite/agent/auth_requests` |
| `conversation_messages` | non migrée → cible `sqlite/workspace` ⚠ |
| `conversations` | non migrée → cible `sqlite/workspace` ⚠ |
| `wait_for` | non migrée → runtime `agent_manager` (concept d'attente) |

### 1.11 `modelweaver_repo.py` / `schema.py` / `exchange_dbs.py` (inline)

| table OLD | sort |
|---|---|
| `system_state` (modelweaver_repo.py) | non migrée → runtime service |
| `tool_usage` (modelweaver_repo.py) | non migrée → runtime usage |
| `classes_outils` (schema.py) | non migrée (doublon catalogue_schema) |
| `meta` (exchange_dbs.py) | non migrée (doublon) |

---

## 2. Domaines NEW (`modules/sqlite/`)

### 2.1 `sqlite/agent` — writer : `agent.py` (Db mode w)

| table NEW | origine |
|---|---|
| `agents` | agents_schema.sql (+ modelweaver_schema.sql) |
| `agent_entrypoints` | agents_schema.sql |
| `agent_metrics` | agents_schema.sql |
| `agent_runtime` | agents_schema.sql |
| `agent_signals` | agents_schema.sql |
| `auth_requests` | agents_repo.py (inline) |
| `meta` | agents_schema.sql / catalogue_genere_schema.sql |

### 2.2 `sqlite/buffer` — writer : `buffer/write.py` (consumer IN = local)

| table NEW | origine |
|---|---|
| `buffer_op` | `global_local_buffer_op` (renommée) — local_catalogue_schema.sql |

### 2.3 `sqlite/genere` — writer : `genere/__init__.py` `db()` (mode w, WAL)

Schéma V4 : `id_data` INTEGER (hash), `path_data` TEXT, `last_modify` epoch.

| table NEW | origine |
|---|---|
| `gen_data` | catalogue_genere_schema.sql |
| `gen_dependance` | catalogue_genere_schema.sql |
| `gen_runs` | catalogue_genere_schema.sql |
| `gen_fichier` | catalogue_genere_schema.sql |
| `gen_config` | catalogue_genere_schema.sql |
| `gen_file_rules` | catalogue_genere_schema.sql |
| `gen_runtime_files` | catalogue_genere_schema.sql |
| `questions` | catalogue_genere_schema.sql |
| `meta` | catalogue_genere_schema.sql / agents_schema.sql |

### 2.4 `sqlite/local` — writer : `local/write.py` (writer dédié, consumer IN buffer)

Schéma V4 : unicité quad `(namespace_id, name, source, version)`.

| table NEW | origine |
|---|---|
| `global_local_data_type` | local_catalogue_schema.sql |
| `global_local_meta` | local_catalogue_schema.sql |
| `global_local_namespace` | local_catalogue_schema.sql |
| `global_local_path` | local_catalogue_schema.sql |
| `global_local_privilege` | local_catalogue_schema.sql |
| `global_local_privilege_condition` | local_catalogue_schema.sql |
| `global_local_security_supervisor` | local_catalogue_schema.sql |
| `global_local_shared_default` | local_catalogue_schema.sql |
| `global_local_source` | **nouvelle** (V4 — sources user/official/enterprise/distant/friend/git_depot) |
| `global_local_mirror_sources` | **nouvelle** (V4 — refs miroirs) |

### 2.5 `sqlite/security` — writer : `security/security.py`

| table NEW | origine |
|---|---|
| `agent_fs_auth` | **nouvelle** (refonte auth FS) |

### 2.6 `sqlite/task` — writer : `task/write.py` / `task/local.py`

| table NEW | origine |
|---|---|
| `tasks` | workspace_schema.sql |
| `task_dependencies` | workspace_schema.sql (+ workspace.py) |
| `ask_new_task` | workspace_schema.sql |
| `task_meta` | **nouvelle** |
| `entry_request` | **nouvelle** (entrée chat/completion du taskflow) |

### 2.7 `sqlite/workspace` — writer : `workspace/__init__.py` (`WorkspaceScope`)

| table NEW | origine |
|---|---|
| `workspaces` | workspace_schema.sql |
| `workspace_config` | workspace_schema.sql |
| `root_tasks` | workspace_schema.sql |
| `tasks` | workspace_schema.sql (partagée avec sqlite/task) |
| `sub_tasks` | workspace_schema.sql |
| `sub_task_dependencies` | workspace_schema.sql (+ workspace.py) |
| `ask_new_task` | workspace_schema.sql (partagée avec sqlite/task) |
| `task_dependencies` | workspace_schema.sql (+ workspace.py, partagée) |
| `task_supervisor_rules` | workspace_schema.sql |
| `task_files` | workspace_schema.sql |
| `task_log` | workspace_schema.sql (absorbé `task_reports`) |
| `task_budget_tracking` | workspace_schema.sql |
| `human_choice` | workspace_schema.sql (+ workspace.py) |
| `chatroom_messages` | workspace_schema.sql (+ modelweaver_schema.sql) |
| `question` | workspace_schema.sql |
| `reponse` | workspace_schema.sql |
| `issues` | workspace_schema.sql |
| `usage_files` | workspace_schema.sql |

---

## 3. Notes / à confirmer

- **`task_reports`** (OLD) : fusionnée dans `task_log` — à valider que le contenu
  est intégralement porté (⚠).
- **`conversations` / `conversation_messages`** : absentes de `sqlite/workspace`
  (qui n'a que `chatroom_messages`) — cible probable workspace, à confirmer (⚠).
- **`wait_for`** : concept runtime `agent_manager.register_wait` (`_wake_for_tasks`)
  — pas de table SQL dédiée, logique d'attente conditionnelle.
- **`wakeup_calls`** : runtime `agent_manager` ; non persisté dans sqlite.
- **Tables partagées** (`tasks`, `task_dependencies`, `ask_new_task`,
  `sub_task_dependencies`, `meta`) : créées par 2 domaines NEW — à confirmer
  qu'il s'agit d'un `CREATE TABLE IF NOT EXISTS` idempotent partagé et non d'un
  doublon de schéma.
- **`domain_writers`** (OLD catalogue_schema) : registre des writers par domaine
  — à déclarer dans `sqlite/catalogue` (⚠ frontière).
- **Frontière catalogue référence vs runtime** (⚠) : les tables de scoring/
  budget/état d'erreur peuvent aller dans `sqlite/runtime` (futur) plutôt que
  `sqlite/catalogue` — à trancher avant génération du schéma.
