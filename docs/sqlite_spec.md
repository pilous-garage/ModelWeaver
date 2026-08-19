# Spécification du module modules/sqlite — MISE À PLAT des domaines

> Inventaire à jour (2026-08-19). Un domaine = une base SQLite + schéma
> versionné + API read/write centralisée. Certains domaines ont un **writer
> dédié** qui effectue la majorité des écritures par bloc.

## Organisation

```
modules/sqlite/
  base.py            # Db (lock d'écriture, tokens, DDL auto), Table, exceptions
  paths.py           # db_path(name) → ~/.modelweaver/<name>.db
  writer_dedie.py    # classe WriterDedie (boucle tick), base des writers dédiés
  <domaine>/
    __init__.py          # db(), db_ro(), get_writer(token) — token si writer dédié
    <domaine>_schema.sql # DDL (CREATE TABLE IF NOT EXISTS)
    read.py              # lectures (retours dict/list)
    write.py             # écritures de base (token vérifié)
    writer_dedie.py      # optionnel : writer dédié / service_tick
```

Règles :
1. Tout accès SQLite passe par `<domaine>/read.py|write.py` ; **aucun SQL hors de modules/sqlite** (y compris dans les writers dédiés, qui orchestrent seulement).
2. Domaine ouvert = `mode="w"` sans write_token ; domaine avec writer dédié = `mode="w"` + write_token.
3. `read.py` = lecture seule (jamais de token) ; `write.py` vérifie le token.
4. Le writer dédié est le SEUL producteur des écritures massives du domaine.
5. La résolution d'adresses (batch/runtime) se fait par le socle `(provider_ref, endpoint_ref, model_endpoint_ref, type_key)` via `info_llm.read.resolve_adresse_id` — égalités exactes, aucun import depuis l'extérieur du module sqlite.

## Tableau maître des 13 domaines

| Domaine | Fichier DB | Token | Writer dédié | Tables déclarées | Base réelle sur disque | État |
|---------|-----------|-------|--------------|------------------|------------------------|------|
| agent | agents.db | ouvert | — | 7 | 11 lignes ✓ (+4 legacy) | migré, + legacy non migré |
| batch | batch.db | write_batch | BatchWriter (complet) | 10 | créée à la volée, vide | DOMAINE prêt, pas encore alimenté |
| buffer | buffer.db | write_buffer | BufferWriter (tick stub) | 1 | 2 ✓ | actif (ingest v4) |
| genere | catalogue_genere.db | ouvert | — | 9 | 9 ✓ | migré |
| info_llm | info_llm.db | write_info_llm | Compacteur (tick stub) | 15 | 15 ⚠ nom table | actif, 1 écart de nom |
| keys | keys.db | KeyManager (vault) | — | 1 | 2 ✓ | actif (vault) |
| local | local_catalogue.db | write_catalogue | LocalWriter (tick stub) | 10 | 38 ✓ (10 global + 28 data) | actif (catalogue v4) |
| runtime_llm | runtime_llm.db | OUVERT | — | 13 | VIERGE (jamais créée) | À BRANCHER (le bridge écrit encore runtime.db v3) |
| score | score.db | write_score | ScoreWriter (tick stub) | 10 | VIERGE | À BRANCHER (scoring encore legacy runtime.db) |
| security | security.db | ouvert | — | 1 | VIERGE | à brancher |
| task | task.db | write_task | — | 5 | créée à la volée, vide | personne ne l'utilise encore |
| workspace | workspace.db | OUVERT | — | 18 | 19 ⚠ (+task_reports) | ÉCRITURE ENCORE LEGACY (sql_old/workspace.py) |

## Détail par domaine

### agent — agents.db (ouverture libre, 7 tables déclarées)
- Tables : meta, agents, agent_runtime, agent_metrics, agent_signals, agent_entrypoints, auth_requests.
- Réel : 11 tables — **4 tables legacy encore présentes** : `agent_fs_auth` (→ schéma security), `conversations`, `conversation_messages`, `wait_for` (ancien agents_schema sql_old).
- API : read.py 13 fn, write.py 16 fn.

### batch — batch.db (write_batch, writer dédié BatchWriter ✓)
- Tables : meta, usage_history_1m/15m/3h/1d/1w/1mo, model_sequence, llm_caller_sessions, archive_processing_report.
- Writer dédié complet (`BatchWriter`, modules/sqlite/batch/writer_dedie.py) : agrégation 1-min + cascade + purge TTL, séquences succès/échec, sessions caller, réconciliation depuis l'archive. Résolution d'adresse par socle quadruple (info_llm, lecture seule).
- Shim compat : `modules/usage/usage_batcher.py` réexporte run_once/reconcile/tick/main + BATCH_MARGIN_SECONDS/CASCADE.
- **Réel : batch.db créée à la volée (vide)** — l'ancien batcher écrit encore archive_processing_report/llm_caller_sessions dans runtime.db v3. À basculer.

### buffer — buffer.db (write_buffer)
- Tables : buffer_op (+ meta).
- API : read 1 fn, write 13 fn (import_ops/import_local/export_local/export/import_included/export_included, resolve_version, mark_status, purge_applied, retry).
- Writer dédié : tick stub (nettoyage/purge). Les uploads sont déclenchés par les scripts d'ingest (modules/ingest/modelsdev.py), pas par le tick.

### genere — catalogue_genere.db (ouverture libre)
- Tables : meta, questions, gen_data, gen_dependance, gen_runs, gen_config, gen_fichier, gen_runtime_files, gen_file_rules.
- Réel 9 = déclaré 9 ✓. API : read 16 fn, write 16 fn.

### info_llm — info_llm.db (write_info_llm, compacteur)
- Tables (15) : meta, catalogue_providers, provider_endpoints, catalogue_models, model_capability, provider_models, provider_models_mapping, provider_endpoint_api_key_type, endpoint_apikeytype_model_adress, alias_model, cost_final, budget_tags, budgets, budget_generique_key_tag, budget_user_key_id.
- QG du bridge : SEUL le compacteur écrit ; le bridge lit (read.py) et résout les adresses par socle quadruple (resolve_adresse_id).
- Table RAM `adress` (modules/sqlite/info_llm/adress.py, `:memory:`) : schéma × clés vault, jamais sur HDD. KeyManager dans modules/sqlite/keys.
- **ÉCART RÉEL** : la base regénérée contient `model_capabilities` (pluriel) mais le schéma/API déclarent `model_capability` (singulier) → un regen `--wipe` alignera la base (l'ancienne table restera sinon, pas de purge).

### keys — keys.db (vault keyring, KeyManager)
- Tables : api_keys (+ meta). Métadonnées en base, **clé en clair jamais sur disque** (keyring OS + fallback Fernet). API read 3 / write 3 + key_manager.py.

### local — local_catalogue.db (write_catalogue, catalogue v4)
- Tables globales (10) : global_local_meta, global_local_source, global_local_mirror_sources, global_local_data_type, global_local_namespace, global_local_shared_default, global_local_path, global_local_privilege, global_local_privilege_condition, global_local_security_supervisor.
- Réel 38 = 10 globales + **28 tables de données dynamiques** (model_official_data, endpoint_data, model_provider_endpoint_data, *_source_and_sharing, *_tag, *_tag_type…) créées par data_table.py.
- API : read 14 fn, write 14 fn (import_local, upsert par data_type, versions par entrée…). Writer dédié : tick stub.

### runtime_llm — runtime_llm.db (OUVERT — écriture per-call par les threads bridge)
- Tables (13) : meta, adresse_runtime, model_call_log, model_call_log_archive, real_call_models, really_used_budget, budget_consumption, budget_final, agent_budget_allocation, adress_error_state, budget_error_state, model_efficacy, endpoint_model_usage.
- **RÉEL : runtime_llm.db n'existe pas encore** — le bridge écrit toujours dans runtime.db v3 (32 tables : tables legacy + tables v3 du batcher/scoreur). Le BatchWriter lit `runtime_llm/read.py` → il est prêt mais n'a rien à agréger tant que le bridge n'est pas basculé.
- API : read 15 fn, write 7 fn (log_model_call, log_real_call, update_adresse_runtime, update_budget_final, upsert_model_efficacy, delete_model_call_log_up_to, archive_model_calls_up_to).

### score — score.db (write_score)
- Tables (10) : meta, score_thinking_power, model_bucket_counts, score_bucket_heads, score_batch_blocks_5m/1h/1d, score_batch, score_benchmark_etire, score_benchmark_meta.
- **Réel : vierge** — le scoring réel vit encore dans modules/usage (score_buckets/score_blocks/score_benchmark) et écrit dans runtime.db v3 ; le BatchWriter l'invoque en try/except (non migré). Writer dédié : tick stub (TODO).

### security — security.db (ouverture libre)
- Tables : agent_fs_auth. Réel : vierge (la table existe dans agents.db legacy).

### task — task.db (write_task)
- Tables : task_meta, entry_request, tasks, task_dependencies, ask_new_task.
- **Réel : créée à la volée, personne ne l'utilise** (le taskflow réel reste sur workspace.db via sub_tasks).

### workspace — workspace.db (OUVERT)
- Tables déclarées (18) : root_tasks, task_log, workspaces, workspace_config, tasks, sub_tasks, sub_task_dependencies, ask_new_task, task_supervisor_rules, task_dependencies, issues, task_files, human_choice, chatroom_messages, question, reponse, usage_files, task_budget_tracking.
- **Réel : 19 tables (+ task_reports, écrite par modules/sql_old/workspace.py)** — l'ÉCRITURE réelle du workspace est encore LEGACY : `AgentsCatalogue/lib/workspacedb/taskflow.py` importe `modules.sql.workspace.WorkspaceDB` qui **n'existe pas** (modules/sql = shim minimal) → l'API des agents (sub_tasks) est actuellement cassée à l'import.
- API domaine : read 6 fn, write 7 fn (tables vides en pratique).

## État réel des bases (~/.modelweaver)

| Base | Tables | Base | Tables |
|------|--------|------|--------|
| agents.db | 11 | local_catalogue.db | 38 |
| buffer.db | 2 | modelweaver.db | 49 (legacy) |
| catalogue.db | 47 (legacy v3) | pause_flags.db | 1 |
| catalogue.remote.db | 16 (legacy) | runtime.db | 32 (legacy v3 + batcher/score) |
| catalogue_genere.db | 9 ✓ | user.db | 1 |
| community.db | 1 | workspace.db | 19 |
| info_llm.db | 15 ⚠ | workspace_test_tmp.db | 17 |
| keys.db | 2 | batch.db / score.db / task.db / security.db / runtime_llm.db | créées à la volée, vides |

## Écarts constatés (chantiers ouverts)

1. **info_llm** : base `model_capabilities` vs schéma `model_capability` → regen `--wipe` requis (sinon doublon de table).
2. **runtime_llm** : domaine prêt mais bridge pas basculé → runtime.db v3 écrit encore ; runtime_llm.db vierge ; le BatchWriter n'agrège rien.
3. **batch / score** : bascule pas faite — l'ancien batcher/scoreur écrit dans runtime.db v3 (archive_processing_report, llm_caller_sessions, model_bucket_counts, score_batch…). Modules/usage non migré (appels try/except dans BatchWriter.run_once étape 8).
4. **workspace** : écriture réelle par modules/sql_old/workspace.py ; la lib AgentsCatalogue/lib/workspacedb/ importe modules.sql.workspace (ModuleNotFoundError) → le domaine sqlite workspace doit prendre le relais (y compris task_reports, absent du schéma).
5. **agents.db** : 4 tables legacy à migrer ou purger (agent_fs_auth → security, conversations/conversation_messages/wait_for → agent).
6. **task / security** : domaines non branchés.
7. **Legacy résiduel** : modules/sql_old/* (catalogue_repo, runtime_repo, modelweaver_repo, agents_repo, workspace), bases catalogue.db / runtime.db / modelweaver.db à basculer puis supprimer, + modules/usage (rates, score_*).

## Vérification des tables

Comparer schémas déclarés vs code, et bases réelles vs schémas :

```bash
# Tables déclarées
find modules/sqlite -name "*schema.sql" -exec grep -h "CREATE TABLE IF NOT EXISTS" {} \; \
  | sed 's/.*CREATE TABLE IF NOT EXISTS //;s/ (.*//' | sort -u
# Tables réelles d'une base
python3 -c "import sqlite3,os;print([r[0] for r in sqlite3.connect(os.path.expanduser('~/.modelweaver/<domaine>.db')).execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\")])"
# Imports legacy restants
grep -rln "modules.sql_old\|from modules.sql\b" --include="*.py" services/ modules/ | grep -v __pycache__
```

## Prochaines étapes (ordre logique)

1. Bascule du bridge LLM sur runtime_llm.db (le BatchWriter devient actif).
2. Bascule batcher/scoreur sur batch.db/score.db (retirer l'étape 8 legacy du BatchWriter ; finir ScoreWriter + BufferWriter/LocalWriter/Compacteur ticks).
3. Portage workspace dans le domaine sqlite (task_reports + fix de AgentsCatalogue/lib/workspacedb sur modules.sqlite.workspace) puis purge sql_old.
4. Alignement info_llm (regen --wipe), migration tables legacy agents.
5. Suppression de modules/sql_old + bases legacy (catalogue.db, runtime.db, modelweaver.db).