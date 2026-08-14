# A-TEST — Carnet : implémenté mais PAS testé

Tout ce qui a été écrit dans la session (data_genere, flow, ticker, watcher)
mais qui n'a PAS été validé en réel (run complet du runtime) ou qui est
incomplet. À vérifier/test en priorité avant de considérer le système fiable.

## 0. RÉSOLU depuis — à rayer au fur et à mesure
- ✅ P0-1 : `get_data_genere` wrapper générique (invalidation récursive) — validé
  (commit 0005998). `ensure_team_petri` est un pont vers `get_team_petri`.
- ✅ petri_runtime (miroir FSM par observation pure) — validé (commit 45e54d8).
- ✅ workflow expandu en data_genere (traçabilité exécution) — validé
  (commit 469746a).
- ✅ **P1 runtime SMOKE TEST** (`tests/smoke_petri_runtime.py`, sans LLM) :
  agent running + pots, step FSM avancé, cohérence types ⊆ pétri statique,
  resolve_entrypoint (main/ask_auth/pause) + flux stack/pop.

## 1. FLUX dans le pétri (C1–C5) — créé, jamais exécuté
- Places `flux_<agent>_{main,pause,cancel,ask_auth,receive_auth,stack}` générées
  dans `build_from_yaml` (`services/taskflow_petri.py`).
- Transitions `stack_*` (interruption → pile) et `pop_*` (reprise).
- Invariant C1 (`check_flux_regularity`) : 1 flux in/out par step — passe car
  le check est PASSIF (pas de flux encore dans les agents yaml réels).
- **Non testé** : C2 (priorité monotone), C3 (pause>cancel>auth>main),
  C4 (preemption stack/pop réelle), C5 (pause gèle le travail) — AUCUN run
  réel ne les a exercés.

## 1b. SWITCH entrypoint en cours de run — partiellement câblé
- `resolve_entrypoint` (au démarrage) validé : signal ask_auth PENDING →
  entrypoint résolu, workflow chargé. MAIS le SWITCH pendant un run actif
  (interruption → stack_state → pop_state) repose sur les signaux
  pause/resume + reprise state_json — PAS exécuté en réel. Bloqué par P3
  (pas d'agents actifs dans les BDD de test).

## 2. `get_data_genere` wrapper générique — JAMAIS implémenté
- Design discuté (validité + régénération + récursion sur les deps), mais les
  `ensure_*` de `services/flow_engine.py` restent séparés (ensure_skill_symbol,
  ensure_team_symbols, ensure_team_petri) — pas unifié par un wrapper commun.

## 3. `resolve_entrypoint` BDD — table + méthode, switch non réel
- Table `agent_entrypoints` (agents_schema) + `resolve_entrypoint()` dans
  `agent_manager/service.py` (priorité pause>cancel>ask_auth>main, trigger
  signal:*).
- `execute()` résout l'entrypoint au DÉMARRAGE du run, mais le SWITCH réel
  (stack_state/pop_state pendant un run, interruption d'un workflow en cours)
  n'est PAS câblé. Aucun agent réel n'a d'entrypoint ask_auth/pause déclaré.

## 4. Fichiers runtime (`gen_runtime_files`) — table + check, non branché
- `register_runtime_glob` / `check_runtime` dans `catalogue_genere.py`
  (vérification inverse au get, mtime max du glob).
- `.conf_gitignore.yaml` : section `runtime_globs` présente.
- **Non branché** : aucun `get()` réel du flow_engine n'appelle `check_runtime`
  avant de renvoyer une data.

## 5. Skills non-python — contract vide, pas de traducteur dédié
- `file/*`, `git/*`, `shell/*`, `system/*` résolus par le traducteur python
  (contract {} = aucun effet taskflow — CORRECT).
- Mais pas de traducteurs dédiés yaml/sql pour les autres langages.

## 6. Validation réelle E2E avec un modèle fiable (bloquant session préc.)
- hy3:free ne conclut pas la découpe (produit des tools d'info sans appeler
  decoupe/ask_intel). Le E2E simulé (mock) passe, le réel dépend du modèle.
- **Diagnostic 2026-08-14 (P3)** : l'infra de run FONCTIONNE —
  `AgentDaemon.call('team:llm-code/decoupeur', 'execute', ...)` atteint bien
  l'API LLM. MAIS les 3 clés en BDD (mistral, llm7, ollama-cloud) sont toutes
  INVALIDES/expirées ("Invalid API Key", "Unauthorized"). Aucune clé dans
  l'env. → **bloqué par CREDENTIALS, pas par le code.**
- **RÉSOLU 2026-08-14** : les clés réelles sont dans **`.env`** (nvidia
  `nvapi-`, google `AQ.Ab`, groq `gsk_`, mistral `cYVA`, opencode-zen
  `sk-Ink`...) — chargées par le DirectBridge AVANT le keyring. Le run réel
  fonctionne :
  `provider_ref=nvidia model_ref=meta/llama-3.1-8b-instruct` → **status: ok**.
  Le decoupeur analyse/découpe réellement. La table api_keys avait perdu la
  liaison (key_value vides) mais le `.env` + keyring la comblent.
- **VALIDÉ E2E RÉEL (2026-08-14)** : le flux taskflow complet tourne de bout
  en bout via `run_completion` :
  entry → analysis → découpe (3 coding + merge) → coder doing → respond →
  **task supervised**. Le modèle conclut la découpe (blocage historique levé).
- **Bug restant** : la découpe crée parfois ses sub_tasks sur une NOUVELLE
  tâche (1608) au lieu de la tâche d'origine (1607) — injection task_id
  (`task_id: 0` du modèle) mal résolue. L'analysis 1607 finalise avec respond
  pendant que la découpe 1608 continue. À corriger : injection workspace_id/
  task_id dans decoupe (le modèle met `default`/`0`).

## 7. Invariants — à re-vérifier après modifications
- `services/taskflow_petri.py --check-invariants` (B2/B3/A4/B5/C1) passe sur
  llm-code, mais tout changement d'agent/team doit re-passer.

## 8. ServiceTicker — branché au daemon, pas testé en live
- `_start_service_ticker(log)` dans `services/api/daemon.py` (file_watcher 60s).
- Singleton/éphémère/permanent/timeout 10 ticks VALIDÉS en test unitaire, mais
  PAS en production (daemon réel).

## 9. Perf scan racine — 58s pour le repo complet
- Le scan complet du projet (16 Go avec .venv/.modelweaver) prend ~58s au
  premier passage. OK si une seule fois, mais à optimiser (prune agressive)
  si on scanne souvent.
