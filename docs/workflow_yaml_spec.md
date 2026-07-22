# Spécification YAML — Steps d'un Workflow FSM

## Format général

Un workflow est une liste de **steps**. Chaque step a `id` et `type`, plus des champs optionnels selon le type.

```yaml
- id: step_id             # unique, [a-z][a-z0-9_-]{2,63}
  type: call              # type de step (voir §Types)
  fn: util/get_env@v1     # selon type
  next: step_suivant      # transition par défaut (optionnel)
  on_error: fallback      # transition si échec (optionnel)
```

## Types

| Type | Rôle | Champs spécifiques |
|------|------|--------------------|
| `llm_call` | Appel LLM direct | `skill_prompt`, `provider_ref`, `model_ref`, `temperature`, `max_tokens`, `output_capture` |
| `call` | Appel d'un skill | `fn` (obligatoire), `inputs`, `capture`, `uses_llm` |
| `tool_call` | (déprécié) appel outil legacy | `tool`, `args`, `output_capture` |
| `switch` | Aiguillage multi-branche | `variable`, `conditions[{operator, value, next}]`, `default` |
| `set_variable` | Affectation | `name`, `value` |
| `for` | Boucle for | `var`, `items` ou `start`/`end`/`step` |
| `while` | Boucle while | `condition{variable, operator, value}` |
| `if` | Condition | `condition{variable, operator, value}` |
| `group` | Groupe séquentiel | *body* = steps enfants |
| `break` | Sortie de boucle | — |
| `continue` | Itération suivante | — |
| `sleep` | Pause | `duration_seconds` |
| `spawn` | Lance un agent | `name`, `role`, `occupation`, `request`, `provider_ref`, `model_ref`, `output_capture` |
| `handoff` | Passe la main à un agent | `to` |
| `agent_call` | Appelle l'entrypoint d'un agent | `agent`, `entrypoint`, `inputs`, `capture`, `on_error` |
| `end` | Fin du workflow | `status`: `SUCCESS` ou `FAILED` |

## Champs transverses

### Transitions
```yaml
- id: step_1
  type: call
  fn: util/sleep@v1
  next: step_2           # step suivant (nom du step cible)
  on_error: fallback     # step si échec (optionnel)
```

### Entrées (inputs)
```yaml
- id: read_src
  type: call
  fn: file/read_file@v1
  inputs:
    path: "src/main.py"
```

### Capture de sortie
```yaml
- id: fetch_data
  type: call
  fn: net/http_get@v1
  capture:
    body: response_data   # sortie.body → variable response_data
```

### LLM (step `call`)
```yaml
- id: gen_code
  type: call
  fn: coding/code_gen@v1
  uses_llm: true          # badge 🧠 dans le graphe
  inputs:
    request: "écrire une fonction parse_csv"
  capture:
    result: code_output
```

### Condition (step `if`/`while`)
```yaml
- id: check_ok
  type: if
  condition:
    variable: "{{_last_call_ok}}"
    operator: TRUTHY
    value: ""
  next: success_path
```

### Aiguillage (step `switch`)
```yaml
- id: route
  type: switch
  variable: "{{action}}"
  conditions:
    - operator: EQUALS
      value: "build"
      next: do_build
    - operator: EQUALS
      value: "test"
      next: do_test
  default: unknown
```

### Boucle for
```yaml
- id: loop_files
  type: for
  var: f
  items: "{{files}}"        # ou items: "{{json.loads(files)}}"
```

### Boucle while
```yaml
- id: wait_ready
  type: while
  condition:
    variable: "{{status}}"
    operator: NOT_EQUALS
    value: "ready"
```

### Groupe
```yaml
- id: setup
  type: group
  # Les steps enfants sont dans une sous-liste (body) en YAML,
  # mais dans le graphe ils sont représentés par des nœuds
  # ayant parentId = "setup".
```

### Appel LLM direct
```yaml
- id: ask_llm
  type: llm_call
  skill_prompt: "Explique ce code : {{code}}"
  provider_ref: openai
  model_ref: gpt-4
  temperature: 0.7
  max_tokens: 2000
  output_capture: explanation
```

### Appel agent
```yaml
- id: delegate
  type: agent_call
  agent: worker_1
  entrypoint: analyse
  inputs:
    target: "src/main.py"
  capture:
    result: analysis
  on_error: handle_failure
```

### Spawn agent
```yaml
- id: spawn_child
  type: spawn
  name: helper
  role: analyst
  occupation: disparate
  request: "Analyse le fichier {{file}}"
  output_capture: child_reply
```

### Handoff
```yaml
- id: handoff_to_chief
  type: handoff
  to: chief_agent
```

## Imbrication (body)

Les steps `for`, `while`, `if`, `group` contiennent un `body` avec une sous-liste de steps :

```yaml
- id: loop
  type: for
  var: i
  items: "{{range(3)}}"
  body:
    steps:
      - id: inside
        type: call
        fn: util/sleep@v1
        inputs:
          duration_seconds: "{{i}}"
```

Dans le graphe, ces `body` steps sont représentés comme des nœuds ayant `parentId: loop`.

## Variables

Les valeurs `{{...}}` sont résolues à l'exécution :
- `{{variable}}` → lit une variable du contexte
- `{{expression}}` → évalue une expression Python simple
- Variables spéciales : `_last_call_ok`, `_last_call_error`, `agent_id`, `request`, `messages`
