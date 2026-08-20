# to-sql-migration.md — Plan de migration `modules/sql` (legacy) → `modules/sqlite`

> Objectif : repartir de l'état stable **avant** la création de `modules/sqlite`
> et `modules/sql_old` (commit `a4276ca`, branche `current-sql-migration`), puis
> ré-implémenter la migration **petit à petit**, en validant à chaque étape que
> RIEN ne casse (démarrage daemon + benchmark fonctionnel `bench_swarm_live`
> puis `run_benchmark_on.py swarm --test humaneval`).
>
> Point de repos de départ : `a4276ca` (« feat(local_catalogue): spec refonte »).
> À ce commit : `modules/sql` (legacy, fonctionnel) existe ; `modules/sql_old`
> et `modules/sqlite` **n'existent pas encore**.

## 0. Principe directeur (à respecter pour CHAQUE domaine)

Conformément à `docs/sqlite_spec.md` et `docs/migration_sql.md` :

1. **Une base = un domaine** sous `modules/sqlite/<domaine>/` avec :
   - `__init__.py` → `db()`, `db_ro()`, `get_writer(token)` (token seulement si
     writer dédié).
   - `<domaine>_schema.sql` → DDL `CREATE TABLE IF NOT EXISTS` versionné.
   - `read.py` → lectures seules (jamais de token).
   - `write.py` → écritures de base (token vérifié) ; **le writer dédié est le
     SEUL producteur des écritures massives** du domaine.
   - `writer_dedie.py` (optionnel) → surclasse `WriterDedie` (base
     `modules/sqlite/writer_dedie.py`), définit `run_once()`, s'enregistre via
     `register(ticker, ...)` avec reprise `modules.sqlite.<dom>.writer_dedie:tick`.
2. **Aucun SQL hors de `modules/sqlite`** (ni dans AgentsCatalogue/services).
3. Recâblage des imports `modules.sql.*` → `modules.sqlite.<domaine>.*` **un
   domaine à la fois**, en validant le benchmark entre chaque.
4. `base.py` : `Db` (lock d'écriture, tokens, DDL auto) ; `close()` no-op sur
   les singletons partagés (flag `_shared`) pour éviter les segfaults
   use-after-free multithread.

## 1. Cartographie des domaines à migrer

Légende : **Origine** = où sont les données/tables au point de départ
(`a4276ca`). **Cible** = `modules/sqlite/<domaine>`. **État** = du dernier WIP
(avant rewind), consultable dans `save/` et `git stash` (`tentative-migration`).

### 1.1 Domaines DÉJÀ câblés côté sqlite dans le WIP (à re-valider proprement)

| Domaine | DB cible | Writer | Origine legacy (`modules/sql`) | État WIP | Cible |
|---------|----------|--------|-------------------------------|----------|-------|
| `local` | local_catalogue.db | `write_catalogue` | `catalogue_local.py` | OK (v4, buffer) | conserver |
| `buffer` | buffer.db | `write_buffer` | `catalogue_local.py` (file) | OK | conserver |
| `info_llm` | info_llm.db | `write_info_llm` | (nouveau) | OK (15 tbl) | conserver |
| `keys` | keys.db | KeyManager | `key_manager` | OK (vault) | conserver |
| `runtime_llm` | runtime_llm.db | OUVERT | `runtime_repo.py` / runtime.db | À BRANCHER | bridge read |
| `score` | score.db | `write_score` | (legacy runtime.db) | À BRANCHER | bridge read |
| `batch` | batch.db | `write_batch` | `usage_batcher.py` | À BRANCHER | bridge read |
| `agent` | agents.db | ouvert | `agents_repo.py` | migré + 4 tbl legacy | nettoyer legacy |
| `workspace` | workspace.db | OUVERT | `workspace.py` | écritures legacy | recâbler read/write |
| `task` | task.db | `write_task` | `taskflow.py` | doublons ws | consolider |
| `genere` | catalogue_genere.db | ouvert | `catalogue_genere.py` | migré | conserver |
| `security` | security.db | ouvert | `agents_schema/fs_auth` | 1 tbl | tokeniser |
| `env` | env.db | — | `paths`/annuaire | OK | conserver |
| `modules` | modules.db | — | discover | OK | conserver |
| `services` | services.db | — | system_registry | OK | conserver |
| `runtime` | runtime.db | — | — | OK | conserver |
| `dialogue_agent` | dialogue_agent.db | — | (nouveau) | OK | conserver |

### 1.2 Frontière critique (le vrai chantier)

À `a4276ca`, `modules/sql` (legacy) contient **tout** :
`agent_repository.py`, `agents_repo.py`, `catalogue_local.py`,
`catalogue_repo.py`, `modelweaver_repo.py`, `orchestration_repository.py`,
`runtime_repo.py`, `schema.py`, `sql_module.py`, `db.py`, `migrations.py`,
`assign_classes.py`, `catalogue_genere.py`, `catalogue_path.py`.

≈ **189 fichiers** importent `modules.sql.*`. Au rewind, TOUS ces imports
pointent vers le legacy fonctionnel (pas de `sql_old`/shim cassé) → le système
démarre et le benchmark passe. La migration consiste à **déplacer progressivement**
chaque responsabilité vers `modules/sqlite/<domaine>` puis à **recâbler les
imports** un domaine à la fois.

## 2. Ordre de migration proposé (steps validables)

Chaque step = 1 domaine migré + imports recâblés + **benchmark vert** avant
de passer au suivant. Ne PAS tout faire d'un coup.

### Step 0 — Baseline & guarde
- Branche `current-sql-migration` @ `a4276ca`.
- Vérifier : daemon démarre, `bench_swarm_live.py` → `ok`, pétri OK.
- Sauvegarder le WIP : `tentative-migration` (stash pushé) + `save/`.

### Step 1 — `base.py` + `paths.py` (socle, sans dépendance)
- `Db` (lock, tokens, DDL auto, `_shared` no-op close), `db_path(name)`.
- Pas de recâblage legacy ici ; purement nouveau socle.

### Step 2 — `local` (catalogue) — terrain connu
- Migrer `catalogue_local.py` → `modules/sqlite/local/` (read/write/buffer).
- Recâbler les imports legacy `modules.sql.catalogue_local`.

### Step 3 — `agent` (agents.db)
- Nettoyer les 4 tables legacy (`agent_fs_auth`→security, `conversations`,
  `conversation_messages`, `wait_for`).
- `AgentDomain` : exposer `db()`/`db_ro()`/`get_writer` + tokeniser.

### Step 4 — `workspace` (workspace.db)
- `WorkspaceDB` → `modules/sqlite/workspace/` (18 tables + task_reports).
- Recâbler `modules.sql.workspace` partout (AgentsCatalogue, supervisor,
  agent_manager). **C'est le domaine le plus importé (~52).**

### Step 5 — `task` (task.db) — consolider les doublons
- `tasks`/`ask_new_task`/`task_dependencies` : choisir task.db comme source,
  supprimer les doublons de workspace.db.

### Step 6 — `security` (security.db) — tokeniser
- `agent_fs_auth` depuis agent legacy → `SecurityDomain` avec token.

### Step 7 — `genere` (catalogue_genere.db)
- Migrer `catalogue_genere.py` → `modules/sqlite/genere/`.

### Step 8 — Bridge `runtime_llm` / `score` / `batch`
- Recâbler le bridge (read-only) depuis le legacy runtime.db vers ces domaines.
- Writers dédiés déjà prêts dans le WIP (`save/`) → ré-importer proprement.

### Step 9 — `keys` (déjà OK), `info_llm` (déjà OK) — ré-intégration propre
- Ré-importer les writers dédiés validés du WIP.

### Step 10 — `env` / `modules` / `services` / `runtime` / `dialogue_agent`
- Déjà en place dans le WIP ; ré-importer par copie depuis `save/` et valider.

## 3. Comment récupérer le travail déjà fait (sans casser)

- **`git stash` (branche `tentative-migration`, poussée distante)** :
  `git stash show -p` pour revoir chaque diff ; `git stash apply` pour
  ré-importer un domaine précis.
- **`save/`** : copies `.bak` des fichiers du WIP pour lecture rapide hors-arbre
  (accès plus direct que le stash). Fichiers listés dans `save/MANIFEST.md`.
- Approche par copie sélective : pour chaque step, on copie le(s) fichier(s)
  utiles depuis `save/` dans `modules/sqlite/<domaine>/`, on recâble les imports
  legacy, on valide le benchmark, puis `git commit` atomique par domaine.

## 4. Critères de succès par step

- `python3 services/api/daemon.py serve` démarre sans ImportError.
- `benchmarks/bench_swarm_live.py --workspace mw-llm-code` → `status=ok`.
- `test_petri.py` → 5 passed.
- (Step 8+) `benchmarks/run_benchmark_on.py swarm --test humaneval --n 3` →
  tâches `supervised`, score humain lisible.
- **Zéro régression** sur le domaine précédent.

## 5. Pièges évités (retours d'expérience de la session cassée)

1. **Segfault daemon** : ne jamais `close()` un singleton SQLite partagé
   (`Db._shared`, `close()` no-op). `modules/sql/workspace/__init__.py db()` et
   `modules/sql/agent/agent.py get_domain()` posent `_shared=True`.
2. **Allocation LLM cassée** : `services/llm_allocation/allocate.py` → blocklist
   `BROKEN_MODEL_REFS` / `BROKEN_PROVIDERS` + `ask_llm.py` garde l'allocation
   existante (`_llm_provider`/`_llm_model`). `google/gemini-2.5-flash` comme
   modèle sain de secours.
3. **Finalisation prématurée** : `_finalize_closed_tasks` doit compter le statut
   `attributed` dans `open_`.
4. **Flood de signaux** : `_send_signal` dédoublonne (pas de ré-insert si un
   signal PENDING identique existe). Sinon `agent_signals` explose (227k lignes).
5. **TEAM_ID en dur** : `services/swarm_llm_manager.py` `TEAM_ID` doit être
   résolu depuis la table `teams` (`team:llm-code` → 4), PAS une constante
   (519) — sinon les tâches swarm-as-llm sont orphelines (team_id divergent).
6. **Catalogue par home** : l'allocation lit `_db_paths()` → `<home>/catalogue.db`
   ; un home de test avec un vieux schéma `model_efficacy` (score_quality vs
   score_chat) rend 0 candidat. Utiliser le vrai catalogue (VACUUM INTO depuis
   `~/.modelweaver/catalogue.db`).
7. **Cross-home** : le service `llm_manager` doit tourner sur le MÊME home que le
   daemon (redémarrer via `SupervisorClient().restart('llm-manager')`).
