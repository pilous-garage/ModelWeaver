# swarm_as_llm — Endpoint OpenAI-compatible

Le swarm ModelWeaver se comporte comme un **modèle LLM unique** pour les
frameworks de benchmark (Inspect, Promptfoo, OpenCompass/LEVAL). On envoie un
prompt, le swarm orchestre ses agents (FSM, greedy, catalogue, chatroom, auth)
et retourne une réponse au format OpenAI standard.

## Endpoint

```
POST http://localhost:<port>/v1/chat/completions
Authorization: Bearer <token-daemon>
Content-Type: application/json
```

Payload envoyé par le benchmark :

```json
{
  "model": "my-swarm-build",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "Écris une fonction qui..."}
  ]
}
```

Réponse renvoyée (format OpenAI) :

```json
{
  "object": "chat.completion",
  "model": "my-swarm-build",
  "choices": [
    {"index": 0,
     "message": {"role": "assistant", "content": "```python\ndef f(): ...\n```"},
     "finish_reason": "stop"}
  ],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

## Sélection du mode par `model`

| modèle contient | mode dev-chat |
|-----------------|---------------|
| `build`         | `build` (génère + exécute) |
| `plan` (défaut) | `plan` (analyse) |

Ex. `model="my-swarm-build"` → mode build ; `model="my-swarm"` → plan.

## Retour des scores (feedback)

Après un run de benchmark, soumettre les scores pour alimenter l'amélioration :

```
POST /v1/bench/submit
{ "suite": "human_eval", "task": "q1", "score": 0.8,
  "passed": 4, "total": 5, "token": "<token-writer>" }
```

`GET /v1/bench/stats?suite=human_eval` → moyenne par suite.

## Brancher un framework

### Promptfoo (le plus simple)

`promptfooconfig.yaml` :

```yaml
providers:
  - id: openai:chat:my-swarm
    config:
      baseURL: http://localhost:9999/v1
      apiKey: <token-daemon>

prompts:
  - "Écris une fonction qui calcule la factorielle de n"

tests:
  - vars: {}
    assert:
      - type: contains
        value: "def factorielle"
```

```bash
npx promptfoo eval
```

### Inspect (UK AIS)

```python
from inspect_ai import Task, eval
from inspect_ai.solver import generate, use_tools
from inspect_ai.model import get_model

model = get_model("openai/my-swarm", base_url="http://localhost:9999/v1",
                  api_key="<token-daemon>")
result = eval(Task(dataset=..., solver=[generate()]), model=model)
```

### SDK OpenAI générique

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:9999/v1", api_key="<token-daemon>")
r = client.chat.completions.create(
    model="my-swarm-build",
    messages=[{"role": "user", "content": "Écris une fonction..."}])
print(r.choices[0].message.content)
```

## Notes

- `stream` non supporté pour l'instant (réponse non-stream). Les frameworks
  se configurent en `stream=False`.
- Auth : le token du daemon (voir démarrage) — les SDK prennent n'importe
  quelle apiKey, mais le daemon compare au vrai token.
- Concurrence : le swarm greedy tourne en threads → plusieurs requêtes
  benchmark parallèles sont possibles.

---

## Team `llm-code`

La team `llm-code` (workspace `mw-llm-code`) est le swarm exposé comme LLM :
le pilote planifie/découpe, les greedy (analyste, planificateur, codeurs,
testeur, reviewer, merger) exécutent. Les tâches créées dans `mw-llm-code`
sont piochées par les greedy de la team.

`services/manifests/teams/llm-code.team.yaml` — chargée automatiquement au
boot (TEAM_MANIFESTS glob).

## Route `test-benchmark-auto`

Exécute un benchmark contre le swarm branché sur la team, et retourne le
rapport complet.

```
POST /v1/test-benchmark-auto
{
  "benchmark_name": "factorial" | "fibonacci" | "inspect",
  "team": "llm-code",
  "restrict_llm": ["google/gemini-2.5-flash"]        # liste de modèles
                OU {"tok_in": 10000, "nb_req": 50}    # budget
                OU "google/gemini-2.5-flash,groq/llama",
  "n": 3,
  "per_task": true,        # détail durées/coûts PAR STEP
  "api_key": "<token-daemon>"
}
```

Rapport retourné :

```json
{
  "status": "ok",
  "benchmark": "factorial", "team": "llm-code",
  "workspace": "mw-llm-code",
  "score": 1.0,
  "notes": [{"type": "result", "task": "fact-1", "ok": true}],
  "usage": {"nb_req": 2, "tok_in": 100, "tok_out": 60, "dollars": 0.02, "time_s": 12.3},
  "providers": {"google/gemini-2.5-flash": {"nb_req": 2, "tok_in": 100, "tok_out": 60, "dollars": 0.02}},
  "duration_s": 12.3,
  "steps": [{"task": "fact-1", "status": "pass", "duration_s": 6.1}]   # si per_task
}
```

### `restrict_llm`
- **Liste de modèles** : `["google/gemini-2.5-flash"]` → tous les autres sont
  exclus de l'allocation (allowlist). Appliqué via `ask_llm.exclude_models`
  jusqu'à `assign_llm`.
- **Budget** : `{tok_in, tok_out, nb_req, dollars, time_s}` → le benchmark
  s'arrête quand une limite est atteinte (note `budget` dans le rapport).
- Les deux peuvent se combiner (`{"models": [...], "tok_in": n}`).

### Benchmarks disponibles
- `factorial`, `fibonacci` : mini-benchmarks intégrés (sans Inspect).
- `inspect` : nécessite inspect_ai installé (`.venv-bench`) — voir
  `benchmarks/run_inspect_swarm.py` pour le branchement complet.
