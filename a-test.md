# A-TEST — Carnet : implémenté mais PAS testé

État réel à la fin de la session (2026-08-14). Ce qui reste à valider/test,
et ce qui a été résolu. Les `✅` = validé (par smoke test ou run réel).

## ✅ RÉSOLU (validé)
- ✅ P0-1 `get_data_genere` (invalidation récursive) — validé (0005998).
  `ensure_team_petri` est un pont vers `get_team_petri`.
- ✅ petri_runtime (miroir FSM par observation pure) — validé (45e54d8).
- ✅ workflow expandu en data_genere (traçabilité exécution) — validé (469746a).
- ✅ P1 runtime SMOKE TEST (`tests/smoke_petri_runtime.py`, sans LLM) —
  agent running + pots, step avancé, cohérence types ⊆ pétri, resolve_entrypoint.
- ✅ P2 perf scan — 58s → **1.8s** (32×, transaction SQLite + cache règles) (be96fd6).
- ✅ P2 skills non-python — les 16 skills utilisés sont python et analysés ;
  les 3 skills `type: llm` (coding/*) ne sont pas référencés par les greedy.
- ✅ P3 run réel E2E — clés dans `.env` (chargées par DirectBridge), le flux
  taskflow complet tourne : entry → analysis → découpe → coding → respond →
  **supervised** (102bc34, 554b1f6).
- ✅ Bug injection task_id — `decoupe` résout workspace/task_id depuis la
  sub_task (`_find_subtask_workspace`), validé en réel (24254e8).

## ⏳ RESTE À FAIRE

### 1. FLUX dans le pétri (C2–C5) — structure créée, exécution réelle non exercée
- Places `flux_<agent>_*` + stack/pop générées dans `build_from_yaml`.
- C1 (1 flux in/out) ✅ passe ; MAIS C2 (priorité monotone), C3
  (pause>cancel>auth>main), C4 (preemption stack/pop), C5 (pause gèle le
  travail) — AUCUN run réel ne les a exercés (aucun agent n'a d'entrypoint
  ask_auth/pause déclaré dans les yaml réels).

### 2. SWITCH entrypoint en cours de run — pas exécuté en réel
- `resolve_entrypoint` ✅ au démarrage (signal ask_auth PENDING → workflow).
- Le SWITCH pendant un run actif (interruption → stack_state → pop_state)
  repose sur pause/resume + state_json — PAS exécuté en réel.

### 3. Fichiers runtime (`gen_runtime_files`) — non branché au get
- `register_runtime_glob` / `check_runtime` ✅ (testés isolément).
- MAIS aucun `get()` du flow_engine n'appelle `check_runtime` avant de
  renvoyer une data. À brancher.

### 4. ServiceTicker — branché au daemon, pas testé en production live
- Singleton/éphémère/permanent/timeout 10 ticks ✅ en test unitaire.
- Le daemon réel le démarre (file_watcher 60s + petri_runtime 10s), mais pas
  observé en conditions réelles longues.

### 5. Invariants — à re-passer après tout changement d'agent/team
- `services/taskflow_petri.py --check-invariants` (B2/B3/A4/B5/C1) passe sur
  llm-code ; tout changement doit re-passer.

### 6. Le run en cours / nettoyage BDD
- Des runs de test ont laissé des tâches (1607/1608 nettoyées, 1609 en cours)
  et des agents `analyst-swarm-*` nombreux dans agents.db. Un `purge` des
  tâches de test est à faire si on veut une BDD propre.

### 7. Observation du run réel 1609 (2026-08-14) — modèle d'exécution insuffisant
- Le flux avance : entry → analysis → **découpe correcte** (3 coding + merge,
  sur la bonne tâche grâce au fix) → coder-senior prend le coding #105.
- MAIS le coder-senior (deepseek-v4-flash) est **perdu dans le filesystem** :
  il appelle `file_read_file|workspace/sub_task_get@v1` (confond le SKILL
  `sub_task_get` avec un fichier), explore `workspace/sessions/swarm-*` au
  lieu d'appeler `workspace_sub_task_get_v1`. 14+ rounds, jamais conclu.
- **Constat** : deepseek-v4-flash fait bien la DÉCOUPE mais est trop faible
  pour l'EXÉCUTION (coder). Le blocage est levé pour l'analyse/découpe, mais
  l'exécution complète nécessite un **modèle plus fort** pour le coder
  (ex. un modèle agentic fiable, restrict_llm par rôle : analysis=flash,
  coding=modèle fort).
- Le run 1609 a été arrêté (coding #105 relâché). À relancer avec un coder
  sur un meilleur modèle.

### 7b. CORRIGÉ : prompt ambigu « lis la description (workspace/sub_task_get@v1) »
- Le prompt des greedy (coder/tester/reviewer/explore) disait « lis la
  description (workspace/sub_task_get@v1) » — le modèle le lit comme un
  CHEMIN DE FICHIER et fait `file_read_file` dessus au lieu d'appeler le
  tool `workspace_sub_task_get_v1`.
- **Fix** : prompts réécrits pour dire « APPELER L'OUTIL
  workspace_sub_task_get_v1 (###tool_call:) » + rappel explicite que les
  skills workspace/* sont des OUTILS, pas des fichiers.
- **Validé en direct** : `decoupe(workspace_id="default"/"todo_cli",
  task_id=0, sub_task_id=109)` crée bien les coding sur la bonne tâche
  (le fix d'injection + le prompt). Le run réel a des interruptions du
  swarm qui font échouer l'exécution du tool (problème de stabilité du
  swarm, pas du fix).

### 7c. 3e problème identifié : le greedy ne boucle PAS pour appliquer le tool
- Run 1611 : le decoupeur produit la découpe (round 0, tool_call valide)
  MAIS le FSM fait `ask_new_task` → `sleep` sans exécuter le tool → l'analysis
  est finalisée en supervised + respond (superviseur) sans coding créé.
- **Constat** : le greedy (agent 659) fait 1 round LLM puis se déshydrate
  SANS appliquer le tool_call produit. C'est un problème du CYCLE DE VIE du
  greedy (break_loop trop tôt / pas de re-loop après le round 0), PAS du fix
  d'injection (validé en direct) ni du prompt.
- **RÉSOLU** : 4 causes racines identifiées et corrigées (commits) :
  1. Parse `###tool_call` sans `###` de fermeture (deepseek l'oublie) → regex
     tolérant ;
  2. Prompt OUTILS tronqué à 1200 → les skills workspace/* étaient coupés
     (le modèle faisait file_read_file sur le skill) → [:4000] ;
  3. Fallback : exécuter les ###tool_call### texte même sans tool_calls API ;
  4. Analyste reliquat (agent 520 team:llm-code/analyst) qui piochait les
     analysis et les closait en "cas simple" avant le decoupeur → filtres
     TERMINATED/STOPPED dans le waker.
  + route `restart_all_service` (POST /v1/) : relance le daemon via os.execv
    (recharge tout le code) — corrige le problème récurrent du daemon qui
    tournait avec l'ancien code.
- **VALIDÉ run réel 1620** : analysis → découpe (3 coding + merge) → les 3
  coding sont passés **supervised tag=done/ok** (le coder code/commit réellement).
  Le flux s'est arrêté au merge #142 (timeout du run_completion 900s + daemon
  qui a arrêté de logger) — le timeout du TEST est trop court pour le réel.

## 8. RESTE À FAIRE après validation 1620
1. **Le merge + respond + supervised finale** : le run 1620 s'est arrêté au
   merge #142 (unattributed) car le daemon a cessé de traiter (log arrêté à
   21:56). À relancer avec un timeout de test plus long (> 900s) pour voir la
   fin complète.
2. **Nettoyage BDD** : ~100 agents `analyst-swarm-*` reliquats dans agents.db
   + tâches de test (1607-1620) + tags done/failure résiduels.
3. **C2–C5 du flux pétri** : preemption stack/pop jamais exercée en réel
   (aucun agent n'a d'entrypoint pause/ask_auth déclaré).
4. **Fichiers runtime** (gen_runtime_files) : check_runtime non branché au get.
5. **get_data_genere** : les ensure_* supervisor/team pas encore migrés dessus.

## 9. Modèle LLM réel (2026-08-14)
- Le run 1620 a utilisé `nvidia/deepseek-ai/deepseek-v4-flash-0731` (le
  modèle par défaut d'assign_llm) + des fallbacks groq/google quand nvidia
  échouait. Il a fait le travail (3 coding supervised).
- **Bug trouvé** : mon restrict_llm pointait vers
  `nvidia/meta/llama-3.1-8b-instruct` qui n'EXISTE PAS sur nvidia (il est
  sur openrouter/kilo/groq/llm7/huggingface/ollama) → le restrict ne
  filtrait rien → assign_llm choisissait au hasard.
- **Fix** : restrict corrigé vers `nvidia/deepseek-ai/deepseek-v4-flash`
  (le modèle qui fonctionne) — validé : ask_llm le respecte pour
  analysis/coding/testing.
- **Performance** : chaque coding prend ~3-4 min (6+ rounds LLM, ~30-60s par
  appel deepseek sur nvidia). Le timeout de 900s du run_completion est juste
  suffisant pour une mission simple ; à augmenter pour les missions longues.

## 10. Scoring LLM — bugs découverts (2026-08-15, à déboguer)
- **Pénalité « jamais appelé »** (`_score_model` ×0.4) : volontaire (évite de
  re-sélectionner des modèles morts jamais testés) mais défavorise les
  nouveaux modèles valides. À reconsidérer (×0.6 ? conditionné ?).
- **Bug sémantique score_batch** : `score_blocks.py` écrit `score_fail_rate`
  = score de FAIL lissé (0.55 pour 4 req/0 fail) et `score_latency` = latence
  brute (2004 ms), mais `allocate.py` lit ces colonnes comme SUCCÈS (1=parfait)
  et score 0-1 (exp). Contrat cassé → le deepseek (bon modèle) est mal classé.
- **model_key None** : 61 modèles (dont deepseek-v4-flash-0731) avaient
  model_key=None → benchmark jamais croisé → score baseline 0.1. CORRIGÉ via
  `python3 scripts/fill_model_keys.py` (le normalisateur gère -0731/:preview).
- **deepseek-v4-flash a un bon benchmark** (score_etire=0.416, model_efficacy
  coding 38.46 / agentic 42.91) mais il est invisible à cause du model_key
  + du contrat score_batch cassé.
- **`get_score(type_request)` manquant** : pas de fonction publique sur le
  bridge qui classe les LLM via _score_model. À ajouter.
- **Bug fixé au passage** : `_score_model` référençait `batch_latency_ms`
  (inexistant) → AttributeError → corrigé en `runtime_latency_ms`.

## 11. Refactor adresse_id (provider×endpoint×model_provider) — À FAIRE
- **Idée** : table `provider_model_address` = répertoire RÉSOLU :
  adresse_id, provider_id/ref, endpoint_id/url, model_id/key,
  provider_model_id, provider_model_name, key_ref. L'appel LLM = adresse
  résolue + clé → plus qu'à construire la requête HTTP.
- **Budgets** : 3 tables — budget(budget_id,type,qt),
  cost(budget_id,adresse_id,type,cost/qt), budget_restant(budget_id,type,qt) ;
  tick de soustraction depuis les logs (ou le bridge, à voir si pas trop
  lourd) + tick de reset des quotas.
- **Clé** : reste dans le keyring (valeur jamais en clair sur disque). Un dump
  RAM expose les clés — inherent (tout agent doit les utiliser en clair).
  Calculer à runtime = aucun gain vs le cache keyring (déjà en mémoire).
  Donc : key_ref pointe vers api_keys, valeur au keyring.
- **Orphelins (94)** : PAS de vrais manques — les modèles existent dans
  catalogue_models. La non-résolution vient de la variante de
  provider_model_name (casse/préfixe) entre score_batch et provider_models.
  Résolvables par model_key (résolution tolérante). 9 sont des entrées vides
  (model='') à purger.
- **Point 1 (key_ref vs identity)** : provider_models_mapping.key_ref =
  identity ('default'), alors que api_keys.ref = UUID stable. La table adresse
  doit séparer : key_ref (ref api_keys) + key_identity (default).
- **get_score → score_adresse → tri → give_adresse** : simplifierait
  l'allocation (le scoring produit directement les adresse_id triés).
