# Carnet-to-check V0.8 — Session graphes → tokens → orchestration

État au 2026-08-09. Ce carnet trace ce qui a été fait, ce qui est resté
de côté, et les prochains chantiers. Les n° de commit pointent vers
`test-npm-dev`.

## 1. Ce qui a été fait (par thème)

### 1.1 Le refactor graphe → modèle token (le tournant)
Le panneau **Graphe Agent** (graphe-agent) devait montrer le FSM des agents
du catalogue. Ça a débouché sur un constat : les équipes définissaient les
agents INLINE au lieu de référencer le catalogue. Refactor en profondeur :

- **Les teams référencent le catalogue** (`592b300`) : champ `ref` dans
  `TeamMemberSpec`/`TeamLeaderSpec` ; `_load_agent_yaml_config` charge
  `{ref}.agent.yaml` en priorité. dev-chat : 15 membres → tous `ref`.
- **8 agents catalogue génériques** : chat-pilot (FSM plan/build),
  greedy-coder, greedy-reviewer, greedy-explore, greedy-tester,
  greedy-analyst, greedy-merger, greedy-coordinateur.
- **16 anciens agents dépréciés** → `*.agent.yaml.old` (plus lus).
- **fix manager@v2/worker@v2** : `entrypoints.start` → `entrypoints.main`.

### 1.2 Modèle token (les tâches = des TOKENS)
`bb61019` + `97a5709` :
- `tasks.role_required` → **`task_type`** (étape du pipeline : coding,
  code_review, merger_code, testing_code, analysis, merge_split,
  exploration). `status` = `todo`/`doing`.
- **`task_dependencies(task_id, parent_id, required_state)`** : dépendance
  TOTALE — un token n'est piochable que si TOUS ses parents sont à l'état
  requis. `parent_id` supprimé de tasks.
- **Skills tokens** (seules portes d'entrée) :
  - `workspace/token_task_pick` : `task_types=[{type, max_difficulty}]`
  - `workspace/token_task_create` : `task` + `parents`
  - `workspace/token_task_modify` : transition (destroy/créer, même task_id)
- **Split A → B,C,D** : B,C,D créés (indépendants), A réaffecté en
  `merge_split` avec `parents=[B,C,D]` → A débloqué quand B,C,D done, puis
  vérifie/merge. Sémantique validée : **les parents viennent avant**.
- `end_exec` : commit → push → **transition coding→code_review** (même si
  le push central échoue, le commit local prouve la livraison).

### 1.3 Cycle de vie complet (tracking git + clear/cancel)
`8543b3c` :
- Colonnes tasks : `commit_start`/`branch_start` (créés avec la tâche ou
  déduits par le 1er picker), `commit_current`/`branch_current` (mis à jour
  à chaque transition), `primordial` (0/1 : racine vs split), `cancelled`.
- **`clear_task`** : supprime la tâche + son groupe (ancêtres secondaires).
  Garde-fous : chaque membre done + parents à l'état requis + aucun membre
  parent d'une autre primordiale.
- **`cancel_task`** : arrête les agents du groupe, protège sur une branche
  `canceled_<task_id>` du repo central local, reset au `commit_start`, flag
  `cancelled`. Ne supprime rien (le code reste).
- chat-pilot : crée des token_task **primordiales**, surveille son groupe,
  clear quand tout est done, cancel si annulation.

### 1.4 Ordonnancement (priorité EDF + anti-famine + scope team)
`9342d92` :
- Colonnes tasks : `deadline` (TEXT) + `estimated_minutes` (INTEGER).
  `created_at` au format SQLite (`YYYY-MM-DD HH:MM:SS`) pour `strftime`.
- **Score de priorité** (à la volée dans l'ORDER BY de claim_next) :
  `base_priority + 100 * urgence(0..10) + MIN(100, 0.1 * âge_min)`
  - urgence : temps estimé long + deadline courte → haute ; **en retard
    (remaining<=0) → 10 immédiat** ; sans deadline → 0.
  - anti-famine : l'âge part du `created_at` de la PRIMORDIALE (les
    sous-tâches héritent via `created_at: {{task.created_at}}` au split).
- **`accept_external_work`** : false → l'agent ne pioche que SA team (jamais
  les tâches projet partagé -1). true → sa team + -1, team d'abord. Une
  tâche -1 n'est jamais appropriée (team_id inchangé).
- `now` passé en paramètre Python (cohérence, pas de fuseau BDD).

### 1.5 Taskflow backend + GUI
`654f8f8` + `19fbe64` :
- `build_taskflow` : consommation = task_types piochés (step pick du FSM),
  génération = `generates` du .agent.yaml. Les agents sans pick ni generates
  (chat-pilot orchestrateur) sont exclus.
- GUI `agentYamlToTaskflow` **step-by-step** : chaque step = nœud avec
  token-in (cyan) / token-out (violet). Détecte pick/create/modify/end_exec
  + corps de boucle while.

### 1.6 Réveil automatique des greedy (le bug bloquant)
- **Bug racine trouvé en test réel** : `token_task_pick` faisait
  `json.loads` sur le `task_types` YAML du FSM (`[{type: coding, ...}]`)
  → échec (clés non quotées) → pick silencieusement vide → les greedy se
  réveillaient, échouaient, se rendormaient en boucle. **Fix** (`b55ce1c`) :
  parsing regex.
- **Bug des services obsolètes** : les process lancés avant un déploiement
  gardent l'ancien code en mémoire (l'ancienne migration cleanait les tasks
  à chaque tick). Fix : redémarrer daemon + agent-manager + watcher +
  installer.
- **État DB pollué** : 2405 wait_for fantômes purgés.
- **Validé en réel** : l'agent-manager réveille coder-a → pick réussi →
  token en `doing`/assigned.

## 2. Ce qui reste de côté (laissé pendant la bifurcation)

1. **`ask_llm(coding_high, agentic)`** : `coding` exige déjà function_calling
   (agentic), mais `coding_high` (score élevé) n'est pas un use_case distinct.
2. **Boucle `continue_exec_task`** : le design verdict
   done/continue/error/to_difficult (task_verdict) + bump difficulty existe,
   mais la boucle FSM `while` n'est pas exécutée de bout en bout en réel.
3. **Check diff non-destructif** d'end_exec (refuse >30% suppressions) :
   testé unitaire, pas sur un vrai run LLM.
4. **GUI taskflow step-by-step** : implémenté + testé, jamais validé
   visuellement.
5. **Chat-pilot plan/build** : utilise encore `workflow/autonomous@v1`
   (l'ancien moteur). La réécriture "intelligente" d'autonomous est reportée.
6. **`accept_external_work: false`** pour auto_improve : le flag existe, pas
   encore configuré au manifest.
7. **Tests GUI** : agentYamlToTaskflow testé, pas les panels texte
   (swarm-taskflow/agent-taskflow) avec les nouveaux types.

## 3. Prochain chantier — Entrypoints prioritaires + cascade d'autorisation

### Le besoin
Les entrypoints d'un agent sont ses **entrées**, de différents types et de
**différentes priorités** :
- signaux système : cancel / pause / break at end of step → gérés par le FSM
- autorisation (cascade à 4 niveaux : membre → team_leader →
  global_security_authorisation → humain) → intercepte avant/pendant un skill

### L'architecture proposée : Contrôleur de Signaux + Exécuteur de FSM
(analogie : le noyau d'un OS — signaux système + syscalls)

Au lieu de vrais sous-threads Python par agent (verrous, concurrence, état
SQL désynchronisé), on sépare en 2 entités logiques dans la boucle :

```
[ Signal extérieur ] ──> (cancel / pause / auth_request)
                              │
                              ▼
               ┌──────────────────────────────┐
               │    Contrôleur de l'Agent     │  (boîte de réception prioritaire,
               │   signal_queue, vérifiée     │   vérifie à chaque itération)
               │   à chaque début de step)    │
               └──────────────┬───────────────┘
                              │
               ┌──────────────▼───────────────┐
               │    Exécuteur de la FSM       │  (exécute les steps)
               └──────────────────────────────┘
```

- L'agent reste une **boucle légère** ; `ExecutionState` persistant en BDD.
- À chaque début de cycle, le Contrôleur lit `signal_queue` : pause →
  bascule d'état sans détruire le contexte ; cancel → protocole de sortie +
  libère la tâche.

### Entrypoints prioritaires dans le .agent.yaml
```yaml
entrypoints:
  sys_cancel:      # Priorité absolue — interrompt le step en cours
    priority: 100
    type: "signal"
    action: "abort_task"
  sys_pause:
    priority: 90
    type: "signal"
    action: "suspend_execution"
  auth_escalation:  # Cascade — intercepte avant/pendant un skill
    priority: 50
    type: "cascade_auth"
    levels:
      - name: "member"        # auto : /home/{{agent.name}}/*
      - name: "team_leader"   # llm_validation + allowed_zones
      - name: "global_security" # forward_to_human (actuellement tout renvoie)
      - name: "human"         # block_and_wait_human
  main:            # flux normal
    priority: 0
    steps: [...]
```

### Cascade d'autorisation en 4 étapes
1. **Tentative** : l'agent veut écrire hors de son /home.
2. **Interception locale** : le skill lève `AuthorizationRequired(level,
   context)`.
3. **Pause de l'exécuteur** : le Contrôleur fige l'état (`AWAITING_AUTH` en
   BDD → le swarm sait qu'il est bloqué).
4. **Résolution cascade** : team_leader (LLM) valide/refuse ; sinon escalade
   global_security (ticket humain).

### Ce qui existe déjà (à réutiliser)
- Signaux : `_make_signal_check` (kill/pause/resume/configure via
  `agent_signals` PENDING) + FSM `_check_signals` + `AgentAbort`.
- Autorisation embryonnaire : skills `comm/ask_authorisation`,
  `team/auth_review`, `team/auth_decide`, `team/is_asked_auth`
  (leader/human, l'agent continue SANS attendre).
- `auth_review@v1`/`auth_decide@v1` dans le bundle pilot.

### Question ouverte
- Comment représenter "global_security_authorisation" (actuellement un
  simple forward vers l'humain) — comme un AGENT dédié (sous-thread) ou un
  service passif ?
- Le team_leader a-t-il une liste statique d'espaces authorisables + un
  appel LLM pour décider, ou un agent redondant ?

## 4. Décisions prises — interruption/reprise (auth_review)

### Le mécanisme : FSM à continuations (co-routine)
Le chat-pilot (= team_leader de dev-chat) reçoit `auth_review` PENDANT qu'il
exécute `entrypoints.main`. Il ne doit pas abandonner son FSM — il se
suspend, traite, reprend.

```
STEP main (pilote en plein travail)
   ├─[début de step] Contrôleur : signal pending ? → auth_review
   ▼
SAUVEGARDER (current_step, variables, messages) sur une pile
   ▼
EXÉCUTER entrypoint auth_review (sous-FSM : décision LLM)
   ▼
RESTAURER (pop) → REPRENDRE main au step suivant
```

Implémentation dans FSMInterpreter :
- `_interrupt(result, handler_steps)` : sauvegarde la position, bascule sur
  le handler ; à la fin du handler (step `end`), `pop` et reprise.
- Équivalent d'un `yield` : le run principal se suspend pour un événement de
  priorité plus haute, puis reprend.

### Décisions validées
1. **auth_review = entrypoint déclarable** du .agent.yaml (avec priorité),
   exécuté en interruption quand un signal arrive. Générique (cancel/pause/
   break aussi).
2. **Contexte du handler RÉDUIT** : on sauvegarde messages/variables/step
   courant, mais le LLM du handler reçoit :
   - la **discussion texte stripée** du reste (pas l'historique complet)
   - un **tool `get_more_context`** pour demander plus d'intel si besoin
     (pas tout le contexte d'un coup)
3. **Tools contextuels PAR STEP** (point clé) : on n'envoie PAS tout le
   bundle de tools à chaque appel LLM. Chaque step déclare les tools dont il
   a besoin. Ex. pour ask_authorisation, seulement :
   - un tool `read` simple (lire le contexte)
   - `authorisation` (yes / no / ask_above)
   - `get_more_context` (intel à la demande)
   - `ask_why` (questionner le membre demandeur)
4. **Chat-pilot = team_leader** de dev-chat : c'est lui qui traite
   auth_review pour les membres de sa team.

### Cascade d'autorisation (rappel)
```
member (auto : /home/agent/*)
  → team_leader (chat-pilot : LLM + espaces authorisables + ask_why)
  → global_security (forward humain, en attendant l'automatisation)
  → human (block_and_wait)
```
Le membre demandeur est en `AWAITING_AUTH` (état BDD) ; le Contrôleur fige
son step, et le re-réveille quand `auth_decide` a posé la réponse.

### Ce qui existe déjà (à réutiliser)
- Signaux : `_make_signal_check` (kill/pause/resume/configure) + FSM
  `_check_signals` appelé à chaque step + `AgentAbort`/pause.
- `_build_llm_tools(bundles)` : déjà étendu pour exposer les tools d'un
  bundle au LLM — à affiner en tools **par step** (le step déclare sa liste).
- Autorisation embryonnaire : `comm/ask_authorisation`, `team/auth_review`,
  `team/auth_decide`, `team/is_asked_auth` (leader/human, sans attendre).

