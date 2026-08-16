# Spécification — Entrypoints d'agent : défauts réservés, hard/soft, signaux

## Contexte

Un agent est défini par un workflow FSM avec des **entrypoints**. Un entrypoint
est un point d'entrée nommé dans le workflow (ex. `main`, `analyse`, `build`).
Historiquement, les agents ne déclarent qu'un entrypoint `main`, et les signaux
de supervision (pause, kill, etc.) sont gérés par le FSM hors workflow.

Cette spec introduit :
1. Des **entrypoints réservés par défaut** : l'agent peut ne PAS les déclarer,
   le FSM inlinera la version par défaut.
2. Le concept **hard/soft** : un entrypoint peut interrompre immédiatement
   (hard) ou attendre la fin de la step en cours (soft).
3. Un **canal de signaux** mappé sur ces entrypoints (tout signal = un entrypoint).

## Format YAML — entrypoints d'un agent

```yaml
entrypoints:
  main:                       # entrypoint principal (déclaré obligatoirement)
    max_iterations: 60
    steps: [...]
  # ── Réservés (optionnels — si absents, la version PAR DÉFAUT est inlinée) ──
  cancel:
    hard: true                # arrêt immédiat (interrompt l'appel LLM/outil)
    steps: [...]              # si omis, steps par défaut (voir §4)
  pause:
    hard: false               # attend la fin de la step en cours
    steps: [...]
  pause_hard:
    hard: true                # interruption immédiate
    steps: [...]
  resume:
    hard: false
    steps: [...]              # reprend après pause
  reset:
    hard: true
    steps: [...]              # reset des variables (pas le home) + relance
  clear_home:
    hard: true
    steps: [...]              # efface le home de l'agent
```

### Champs d'un entrypoint
| Champ | Défaut | Rôle |
|-------|--------|------|
| `hard` | `false` | true = interruption immédiate ; false = on laisse finir la step en cours (appel LLM, outil lourd, script) puis le FSM reprend la main |
| `max_iterations` | 100 | limite d'itérations de l'entrypoint |
| `steps` | — | liste des steps FSM de l'entrypoint |

## Règles hard/soft

- **hard** : le workflow courant est interrompu IMMÉDIATEMENT (l'appel LLM en
  cours, l'outil lourd, le script en cours sont interrompus). L'entrypoint hard
  prend la main sur la step en cours.
- **soft** : on LAISSE FINIR la step en cours (la fonction en cours s'achève :
  fin de l'appel LLM, fin de l'outil, fin du script) ; le FSM reprend la main
  à la fin de cette step et exécute l'entrypoint soft.
- Priorité d'arrivée d'un nouvel entrypoint :
  * si le nouvel entrypoint est **hard** → interruption immédiate du flow
    courant (équivalent pause_hard du flow courant, puis exécution).
  * si **soft** → on attend la fin de la step courante, puis on bascule.

## Pile d'états (stack) — gestion des entrypoints

La gestion des entrypoints repose sur une **pile d'états** (l'agent a un
`ScopeStack` : le dernier élément = scope/état COURANT, les précédents = états
parent/contexte). Un entrypoint signal (cancel/pause/reset...) manipule cette
pile :

- **Entrée d'un entrypoint signal** : on **empile** un nouvel état (le flow
  courant est suspendu en-dessous dans la pile, ses variables conservées).
- **Sortie / retour** : après l'entrypoint, on **dépile** pour revenir à l'état
  précédent (ou on termine le run si l'entrypoint est terminal).
- **hard** : on interrompt la step courante et on pousse le nouvel état
  immédiatement (le flow courant reste en bas de pile, prêt à reprendre après
  resume si l'entrypoint le permet).
- **soft** : on laisse la step courante se terminer, PUIS on pousse le nouvel
  état.
- **reset** : on dépile tout jusqu'à l'état racine (agent), on efface les
  variables (hors identité), on pousse l'état de l'entrypoint ciblé.

Exemple — cancel pendant un run `main` :
```
pile (avant) : [agent, main:step_do_work]
cancel (hard) : interrompt step_do_work → pile (pendant) : [agent, main, cancel]
→ le run se termine avec status CANCELLED (cancel est terminal).
```

Exemple — resume après pause :
```
pile (avant pause) : [agent, main:step_llm]
pause (soft) : step_llm se termine → pile : [agent, main, pause]
resume (soft) : on dépile pause → pile : [agent, main] → on continue main.
```

## Canal de signaux → entrypoints

Tout **signal** est un entrypoint. Les signaux de supervision mappent sur les
entrypoints réservés :

| Signal | Entrypoint | hard | Comportement |
|--------|-----------|------|--------------|
| `cancel` | `cancel` | hard | Arrêt complet : interrompt calls LLM, finalise la tâche en `cancelled_done` |
| `pause_hard` | `pause_hard` | hard | Interruption immédiate, suspend le run |
| `pause` | `pause` | soft | Attend la fin de la step courante puis suspend |
| `resume` | `resume` | soft | Reprend un run suspendu |
| `reset` | `reset` | hard | Efface les variables de l'agent (PAS le home), relance à l'entrypoint ciblé |
| `clear_home` | `clear_home` | hard | Efface le home de l'agent |
| `kill` | (géré FSM) | hard | Arrêt brutal (existant) |

## Steps par défaut des entrypoints réservés

### cancel
```yaml
steps:
  - id: cancel_agent
    type: end
    status: CANCELLED
```
Pendant l'exécution : le FSM doit interrompre tout appel LLM/outil en cours
(lever une interruption), puis le run se termine avec un statut `cancelled`.
Le supervisor marque ensuite la tâche `cancelled_done` → `cancelled_supervised`.

### pause (soft)
```yaml
steps:
  - id: pause_wait
    type: end
    status: PAUSED
```
Le FSM a attendu la fin de la step courante (soft) avant d'exécuter cet
entrypoint. Le run passe en `PAUSED` ; il reprend via `resume`.

### pause_hard (hard)
Idem `pause` mais le FSM a interrompu immédiatement la step courante.

### resume (soft)
```yaml
steps:
  - id: resume_continue
    type: end
    status: RUNNING
```
Reprend le run après une pause (les variables sont conservées).

### reset (hard)
```yaml
steps:
  - id: reset_vars
    type: call
    fn: workflow/reset_variable_after_change_task@v1
    inputs:
      agent_id: "{{agent_id}}"
  - id: reset_end
    type: end
    status: RESET
```
Le reset efface les variables du run (hors identité : agent_id, home,
workspace_id, team_id, project_id) mais PAS le home. Après reset, le FSM
relance l'agent à l'entrypoint ciblé par le signal (payload.entrypoint ou main).

### clear_home (hard)
```yaml
steps:
  - id: clear_home_run
    type: call
    fn: host/clear_home@v1   # à créer : efface agent_home/<id> (hors log ?)
  - id: clear_end
    type: end
    status: RESET
```

## Inlining des défauts

À l'expand du workflow (expand_workflow), pour chaque entrypoint réservé
(`cancel`, `pause`, `pause_hard`, `resume`, `reset`, `clear_home`) NON déclaré
dans `entrypoints`, on injecte la version par défaut (steps + hard). Si l'agent
le déclare, sa version prévaut.

Le résolveur d'entrypoint (resolve_entrypoint) lit aussi le flag `hard` de
l'entrypoint choisi pour décider du mode d'interruption (hard/soft) avant
l'exécution.

## Statuts de tâche liés au cancel

Ajouter les statuts de tâche : `cancelled_done`, `cancelled_supervised`.
- `cancelled` (flag ou statut initial) : le supervisor envoie le signal cancel.
- L'agent finalise → `cancelled_done`.
- Le supervisor marque → `cancelled_supervised` (terminal).

## Priorité

Les entrypoints réservés (signal) ont priorité sur `main`. `cancel` a la plus
haute priorité, puis `pause_hard`, `pause`, `reset`, `resume`. Un signal hard
préempte toujours le flow courant.

## À noter

- `clear_home` nécessite un skill `host/clear_home@v1` (à créer).
- `reset` réutilise le skill existant `reset_variable_after_change_task`.
- Les statuts `PAUSED`/`RESET`/`CANCELLED` du FSM : à ajouter au modèle d'état
  du run (petri_runtime / FSMResult.status).
