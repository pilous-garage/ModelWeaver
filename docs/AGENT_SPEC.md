# Déclaration Agent — Spécification Finale

## Noyau (suffit pour exister)

```yaml
name: string                    # unique, [a-z][a-z0-9_-]{2,63}
role: string                    # référence vers un rôle YAML
occupation: continue | noncontinue | disparate   # défaut: noncontinue
```

## Extensions (on empile)

```yaml
personality:                    # traits, ton, style
  tone: string
  formality: string
  traits: list[string]

context:                        # son monde
  project: string
  domain: string
  team: list[string]

data:                           # données de référence
  - type: doc | convention | spec
    path?: string
    content?: string

variables:                      # mémoire de travail
  key: value

fsm:                            # automate à états
  initial: string
  states: { state: { on: event → next_state, ... } }
  effects: { transition: { set, incr, ... } }

resources:                      # besoins système
  llm: bool                     # a besoin d'un LLM ?
  ram: {min: string, max: string}   # ordre de grandeur
  cpu: {min: string, max: string}
  priority: int                 # 0-10
  preemptible: bool

channels: [queue, chatroom, todo]

signals:
  listen: [pause, status, kill, health]
  parallel: bool

tools:
  - type: llm | file | email | ...
    operations: list[string]

schedule: always_on | on_demand
```

---

# Phase 1 — Minimal Viable Agent

## Objectif
Un agent existe en BDD, peut être hydraté/déshydraté, exécute une tâche simple
via le Bridge LLM. Thread OS, pas de FSM complexe.

## Tables BDD

```sql
-- agents — identité persistante (remplace l'ancienne)
CREATE TABLE IF NOT EXISTS agents (
    agent_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT UNIQUE NOT NULL,
    ref           TEXT UNIQUE NOT NULL,
    role_type     TEXT NOT NULL,
    occupation    TEXT NOT NULL DEFAULT 'noncontinue'
                  CHECK(occupation IN ('continue', 'noncontinue', 'disparate')),
    status        TEXT DEFAULT 'INIT'
                  CHECK(status IN ('INIT', 'IDLE', 'RUNNING',
                                   'STOPPED', 'TERMINATED')),
    config_json   TEXT,
    resources_json TEXT,
    variables_json TEXT,
    state_json    TEXT,
    successor_id  INTEGER REFERENCES agents(agent_id),
    created_at    TEXT DEFAULT (datetime('now')),
    last_active_at TEXT
);

-- agent_runtime — threads actifs
CREATE TABLE IF NOT EXISTS agent_runtime (
    agent_id      INTEGER PRIMARY KEY REFERENCES agents(agent_id),
    thread_id     TEXT UNIQUE,
    pid           INTEGER,
    heartbeat_at  TEXT,
    started_at    TEXT,
    current_step  TEXT
);
```

## Interface

```python
class Agent:
    @staticmethod
    def hydrate(agent_id: int, db: DB) -> Agent
        # charge BDD, vérifie existence, crée thread

    def execute(self, request: str) -> dict
        # appelle le Bridge LLM, retourne la réponse

    def dehydrate(self)
        # sauve état, tue le thread

    def get_status(self) -> AgentStatus
        # état courant

    def to_dict(self) -> dict

class AgentManager:
    def list_active(self) -> list[AgentHandle]
    def check_heartbeats(self, max_age: int = 30) -> list[int]
        # retourne les IDs des agents sans heartbeat récent
    def kill(self, agent_id: int)
```

## Ticker → Agent Manager

```python
ticker.tick():
    alive = agent_manager.check_heartbeats()
    if agent_manager not in alive:
        restart(agent_manager)
    # rien d'autre. le ticker ne voit pas les agents.
```

## Flux minimal

```
1. AgentFactory.create_agent(name, role, occupation)
   → INSERT agents (INIT)

2. Agent.hydrate(agent_id)
   → SELECT agents → UPDATE status=IDLE
   → INSERT agent_runtime (thread_id, pid, heartbeat_at=now)

3. agent.execute("génère du code")
   → UPDATE status=RUNNING
   → Bridge.chat(provider, model, messages)
   → UPDATE status=IDLE, state_json, variables_json
   → Retourne le résultat

4. agent.dehydrate()
   → UPDATE last_active_at, status=INIT
   → DELETE agent_runtime
   → Thread meurt

5. AgentManager.check_heartbeats()
   → SELECT * FROM agent_runtime WHERE heartbeat_at < now-30s
   → Pour chaque: marque comme zombie, signal warning
```
