# Spécification Agent ModelWeaver V2

> Document de conception — V2
> Définit avec précision ce qu'est un Agent, ses limites,
> sa déclaration, son cycle de vie et sa gestion.

---

## 1. Définition fondamentale

Un **Agent** est une **identité persistante** qui couple :

- Un **Rôle** (comportement, compétences, prompt système)
- Un **Automate à états** (FSM) — son moteur d'exécution
- Un **État** (variables, mémoire, sessions)
- Un **Cycle de vie** (naissance, exécution, sommeil, succession, mort)

> Le LLM n'est pas un attribut de l'Agent. C'est un **outil** comme un autre,
> rendu disponible par le FSM Interpreter au même titre que `read_file`
> ou `check_email`.

Trois modes d'occupation :

| Occupation | Comportement | Exemples |
|------------|-------------|----------|
| **continue** | Toujours hydraté, toujours actif | architecte, agent-manager, email-checker |
| **noncontinue** | Hydraté pour une tâche, déshydraté après | codeur, critique, traducteur |
| **disparate** | Rare, réveillé ponctuellement, BDD la plupart du temps | expert légal, auditeur sécurité, formateur |

> Principe Phénix : l'Agent n'existe qu'en BDD. Il est hydraté à la
> demande, exécute son FSM, puis se déshydrate. Pas de processus résident.
> L'OS gère le multithreading.

---

## 2. Déclaration d'un Agent

### 2.1 Noyau minimal (suffit pour exister)

```yaml
name: string                    # Identifiant unique ([a-z][a-z0-9_-]{2,63})
role: string                    # Référence vers un rôle déclaré YAML
occupation: continue | noncontinue | disparate  # Mode d'occupation (défaut: noncontinue)
```

### 2.2 Extensions (on empile au besoin)

```yaml
name: "architecte-principal"
role: "architecte"
occupation: continue           # continue | noncontinue | disparate

# ── CE QU'IL EST (identité) ──
personality:
  tone: "précis et pédagogique"
  formality: "formel"
  traits: ["analytique", "synthétique", "directif"]

context:
  project: "ModelWeaver"
  domain: "développement LLM, architecture logicielle"
  team: ["orchestrateur", "codeurs", "QA"]

data:
  - type: "doc"
    path: "docs/ARCHITECTURE.md"
  - type: "convention"
    content: "Python 3.11+, clean architecture"

# ── SES VARIABLES INTERNES (mémoire de travail) ──
variables:
  current_project: "ModelWeaver"
  dans_une_boucle: false
  tâches_parallèles: 0

# ── SON AUTOMATE À ÉTATS (FSM) ──
fsm:
  initial: veille

  states:
    veille:
      description: "En attente de travail"
      on: nouvelle_tâche → analyse

    analyse:
      description: "Analyse la demande"
      on:
        plan_complet → exécute
        boucle_détectée → critique

    exécute:
      description: "Produit le livrable"
      on:
        livré → veille
        bloqué → plan_b

    critique:
      description: "Réévalue l'approche"
      on:
        plan_révisé → exécute
        abandon → veille

  effects:
    veille → analyse:          {set: {dans_une_boucle: false}}
    analyse → critique:        {incr: tâches_parallèles}
    critique → exécute:         {set: {dernière_idée: "$event.reason"}}

# ── RESSOURCES NÉCESSAIRES ──
resources:
  llm: true                     # vrai → Organisateur alloue un LLM
  ram:                          # ordre de grandeur pour le scheduler
    min: "100Mo"
    max: "500Mo"
  cpu:                          # intention d'usage
    min: "10%"
    max: "40%"

# ── SES CANAUX (communication) ──
channels:
  - queue
  - chatroom
  - todo

# ── SIGNAL CHANNEL (canal de supervision parallèle) ──
signals:
  listen: [pause, status, kill, health]
  parallel: true               # Reçu sans interruption de la tâche courante

# ── OUTILS DISPONIBLES ──
tools:
  - type: llm
    provider: openai
    model: gpt-4o-mini
  - type: file
    operations: [read, write]

# ── SCHEDULE (disponibilité) ──
schedule:
  type: always_on              # ou on_demand
```

### 2.3 Exemple NoLLM (automate pur)

```yaml
name: "email-checker"
role: "watcher"
occupation: continue

resources:
  llm: false
  ram: {min: "10Mo", max: "50Mo"}
  cpu: {min: "1%", max: "5%"}

fsm:
  initial: écoute

  states:
    écoute:
      description: "Surveille les nouveaux emails"
      on: email_reçu → filtre
    filtre:
      description: "Analyse l'expéditeur et le sujet"
      on:
        urgent → notifier
        spam → archive
        normal → veille
    notifier:
      description: "Prévient l'équipe"
      on: notifié → archive
    archive:
      description: "Classe l'email"
      on: archivé → écoute

tools:
  - type: email
    operations: [check_new, read, archive]
  - type: notification
    operations: [send_push, send_slack]
```

### 2.4 Exemple Service Agent

```yaml
name: "agent-manager"
role: "supervisor"
occupation: continue

resources:
  llm: false
  ram: {min: "5Mo", max: "20Mo"}
  cpu: {min: "1%", max: "5%"}

fsm:
  initial: checkin

  states:
    checkin:
      description: "Vérifie les agents actifs"
      on: agents_ok → sleep
      on: agent_mort → signaler

    signaler:
      description: "Marque l'agent comme déserté"
      on: signalé → sleep

    sleep:
      description: "Attend le prochain cycle"
      on: tick → checkin

tools:
  - type: supervisor
    operations: [check_thread, list_active, kill_agent, send_signal]
```

---

## 3. Architecture globale

```
┌─────────────────────────────────────────────────────┐
│ Ticker                                              │
│ Timer système — vérifie que l'Agent Manager est vif │
│ Rien d'autre. Ne voit jamais les agents.            │
└────────────────────┬────────────────────────────────┘
                     │ (un seul point de contact)
                     v
┌─────────────────────────────────────────────────────┐
│ Agent Manager (service agent)                        │
│ Superviseur : existence, travail, signaux, kill      │
│ Ne fait pas d'orchestration, pas de qualité.         │
└───────┬──────────────┬──────────────┬────────────────┘
        │              │              │
        v              v              v
   Agent A          Agent B        Agent C
   (thread)         (thread)       (thread)
   hydrate→fsm→die  hydrate→fsm→die hydrate→fsm→die
```

### 3.1 Rôles des composants

| Composant | Responsabilité | Scope |
|-----------|---------------|-------|
| **Ticker** | Timer système, check Agent Manager | Infrastructure |
| **Agent Manager** | Supervision des threads agents | Gestion |
| **Agent** | Exécution autonome de son FSM | Travail |
| **FSM Interpreter** | Exécute le graphe d'états | Moteur |
| **Organisateur** | Assigne les ressources LLM aux agents | Orchestration |
| **Tool Providers** | Fournissent les outils (bridge, file, email...) | Services |

---

## 4. Cycle de vie d'un Agent (thread hydraté)

```
                  ┌──────────┐
                  │  INIT    │ (ligne BDD créée)
                  └────┬─────┘
                       │ Organisateur/réveil
                       v
                  ┌──────────┐
         ┌───────│ HYDRATE   │ (thread créé, état chargé)
         │       └────┬─────┘
         │            │ vérification identité BDD
         │            v
         │       ┌──────────┐
         │       │ RUNNING   │ ←── FSM Interpreter tourne
         │       └────┬─────┘
         │            │
         │       ┌────┴─────┐
         │       │          │
         │    FSM OK     FSM FAIL
         │       │          │
         │       v          v
         │  ┌────────┐  ┌────────┐
         │  │ IDLE   │  │ FAILED │
         │  └────┬───┘  └────────┘
         │       │            │
         │       v            v
         │  ┌──────────┐
         │  │DEHYDRATE │ (état→BDD, thread meurt)
         │  └────┬─────┘
         │       │ enregistre last_active_at
         │       v
         │  ┌──────────┐
         │  │ TERMINATED│
         └──┤(ligne BDD)│
            └──────────┘
                     │
                     ├─ (si successeur) → transfère sessions → TERMINATED
                     └─ (sinon) → STOPPED

      SIGNAL CHANNEL (parallèle à tout le cycle)
      ─────────────────────────────────────────
      ┌─────────────┐
      │  Agent      │←──── pause? → acknowledge
      │  (travail)  │←──── status? → "j'en suis là"
      │             │←──── kill → cleanup + die
      └─────────────┘
```

### 4.1 États

| Statut | Description |
|--------|-------------|
| `INIT` | Ligne BDD créée, jamais hydraté |
| `HYDRATE` | Thread créé, état chargé depuis BDD |
| `RUNNING` | FSM Interpreter actif |
| `IDLE` | FSM terminé, en attente de déshydratation |
| `FAILED` | FSM échoué |
| `DEHYDRATE` | État écrit en BDD, thread va mourir |
| `STOPPED` | Ligne BDD, agent mort proprement |
| `TERMINATED` | Ligne BDD, agent mort avec successeur |

### 4.2 Signaux (canal parallèle)

| Signal | Payload | Effet |
|--------|---------|-------|
| `pause` | `{reason, duration?}` | L'agent finit son étape courante puis suspend le FSM |
| `resume` | `{}` | Reprend le FSM là où il était |
| `status` | `{}` | Répond avec variables + état FSM courant |
| `health` | `{}` | Répond avec santé + heartbeat |
| `kill` | `{reason, force?}` | Cleanup + mort immédiate ou à la prochaine étape |
| `configure` | `{key, value}` | Modifie une variable interne |

Un agent qui reçoit un signal répond via `agent_signals` table ou via un
callback direct si le thread est en mémoire. L'acquittement est garanti
(soit synchrone si thread actif, soit différé si déshydraté).

---

## 5. Interface d'un Agent

```python
class Agent:
    # ── Cycle de vie (Agent Manager / Organisateur → Agent) ──
    def hydrate(self, alloc: ResourceAllocation) -> Self
    def execute(self, task: Task) -> ExecutionResult
    def dehydrate(self) -> None
    def terminate(self, successor_role: str | None = None) -> ExitResult

    # ── Signaux (canal parallèle) ──
    def send_signal(self, signal: Signal) -> SignalAck
    def poll_signals(self) -> list[Signal]

    # ── État ──
    def get_status(self) -> AgentStatus
    def get_config(self) -> AgentConfig
    def save_state(self, state: dict)
    def restore_state(self) -> dict

    # ── Introspection ──
    def to_dict(self) -> dict
    def get_role_definition(self) -> RoleDefinition
    def health(self) -> HealthStatus
```

### 5.1 Contrat de l'Agent Manager

```python
class AgentManager:
    def list_active(self) -> list[AgentHandle]
    # Agents qui ont un thread en vie (hydratés)

    def check_zombies(self) -> list[int]
    # Agents en RUNNING mais thread mort (plus d'heartbeat)

    def send_signal(self, agent_id: int, signal: Signal) -> SignalAck
    def kill(self, agent_id: int, force: bool = False)
    # Signaux de supervision

    def heartbeat(self, agent_id: int)
    # Appelé périodiquement par le thread agent
```

### 5.2 Contrat de l'Organisateur

```python
class Organisateur:
    def allocate(self, agent: Agent, task: Task) -> ResourceAllocation
    # Assigne un LLM ou un tool provider à l'agent pour cette tâche
    # Peut changer entre deux tâches (ex: groq saturé → openai)
```

---

## 6. FSM Interpreter — Moteur universel

Le seul moteur d'exécution. Tous les agents passent par lui.

### 6.1 Steps disponibles

| Step | Condition | Description |
|------|-----------|-------------|
| `llm_call` | resources.llm=true | Appelle le Bridge via le provider alloué |
| `tool_call` | toujours | Appelle un tool provider (file, email, etc.) |
| `switch` | toujours | Branchement conditionnel selon variables |
| `sleep` | toujours | Attente (timeout, durée) |
| `send_signal` | toujours | Envoie un signal à un autre agent |
| `check_condition` | toujours | Évalue une expression logique pure |
| `spawn` | occupation=continue | Crée un agent enfant |
| `set_variable` | toujours | Modifie une variable interne |
| `concat` | toujours | Concatène des données (pipelines) |
| `extract` | toujours | Extrait des données structurées |
| `end` | toujours | Termine le FSM |

### 6.2 Contrat

```python
class FSMInterpreter:
    def run(
        self,
        fsm_definition: dict,      # la partie fsm: de la déclaration
        variables: dict,            # état courant
        tool_registry: ToolRegistry, # tous les providers disponibles
        event: str | None = None,   # événement déclencheur
    ) -> ExecutionResult
```

---

## 7. BDD — Tables runtime

### 7.1 Table `agents` (identité persistante)

```sql
CREATE TABLE agents (
    agent_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT UNIQUE NOT NULL,
    ref           TEXT UNIQUE NOT NULL,  -- "agent:{name}"
    role_type     TEXT NOT NULL,
    occupation    TEXT NOT NULL DEFAULT 'noncontinue'
                  CHECK(occupation IN ('continue', 'noncontinue', 'disparate')),
    status        TEXT DEFAULT 'INIT'
                  CHECK(status IN ('INIT', 'HYDRATE', 'RUNNING', 'IDLE',
                                   'FAILED', 'DEHYDRATE', 'STOPPED', 'TERMINATED')),
    config_json   TEXT,             -- personality, context, data, tools, channels, signals
    resources_json TEXT,            -- {llm: bool, ram: {min, max}, cpu: {min, max}}
    fsm_json      TEXT,             -- fsm definition
    variables_json TEXT,            -- variables internes
    state_json    TEXT,             -- état FSM courant (state courant, historique)
    successor_id  INTEGER REFERENCES agents(agent_id),
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now')),
    last_active_at TEXT             -- dernière déshydratation complète
);
```

### 7.2 Table `agent_runtime` (threads actifs)

```sql
CREATE TABLE agent_runtime (
    agent_id      INTEGER PRIMARY KEY REFERENCES agents(agent_id),
    thread_id     TEXT UNIQUE,         -- identifiant OS du thread
    pid           INTEGER,             -- process ID
    host          TEXT,                -- hostname
    heartbeat_at  TEXT,                -- dernier heartbeat
    started_at    TEXT,                -- début d'hydration
    current_step  TEXT,                -- step FSM en cours
    allocated_to  TEXT,                -- provider LLM alloué (json: {ref, model})
);
```

### 7.3 Table `agent_signals` (canal de supervision)

```sql
CREATE TABLE agent_signals (
    signal_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id      INTEGER NOT NULL REFERENCES agents(agent_id),
    type          TEXT NOT NULL
                  CHECK(type IN ('pause', 'resume', 'status', 'health',
                                 'kill', 'configure')),
    payload_json  TEXT,                -- données du signal
    status        TEXT DEFAULT 'PENDING'
                  CHECK(status IN ('PENDING', 'ACKED', 'COMPLETED', 'FAILED')),
    created_at    TEXT DEFAULT (datetime('now')),
    acknowledged_at TEXT,
    completed_at  TEXT
);
```

### 7.4 Table `agent_metrics` (stats)

```sql
CREATE TABLE agent_metrics (
    agent_id       INTEGER PRIMARY KEY REFERENCES agents(agent_id),
    total_tasks    INTEGER DEFAULT 0,
    failed_tasks   INTEGER DEFAULT 0,
    total_tokens   INTEGER DEFAULT 0,
    total_runtime_ms INTEGER DEFAULT 0,
    avg_latency_ms REAL DEFAULT 0,
    last_updated   TEXT DEFAULT (datetime('now'))
);
```

### 7.5 Tables existantes conservées

- `sessions` — fils de discussion persistants
- `agent_messages` — mémoire brute au format OpenAI
- `wakeup_calls` — tâches (renommable en `agent_tasks`)
- `agent_queue` — messagerie inter-agents
- `chatroom_messages` — board public
- `shared_tasks` — todo partagé
- `agent_connections` — branchements persistants
- `model_providers` — ressources LLM (inchangé)

---

## 8. Sécurité

### 8.1 Périmètre d'accès

- Un Agent n'a **pas** accès aux clés API (le FSM Interpreter les résout)
- Un Agent n'a **pas** accès à la BDD directement (passe par l'Interpreter)
- Un Agent n'a **pas** accès au filesystem hors de son workspace
- Un Agent n'exécute que les steps définis dans son FSM

### 8.2 Isolation

Les agents sont des threads Python. Pas de sous-processus par agent.
L'isolation est logique (variables scope), pas système.

---

## 9. Principe de déclaration évolutive

La déclaration d'un agent suit le principe du **noyau + extensions** :

```
V0 : name + role + type           (suffit pour exister)
V1 : + personality + context      (ce qu'il EST)
V2 : + variables + fsm            (son cerveau)
V3 : + channels + signals         (sa communication)
V4 : + tools                      (ses compétences)
V5 : + schedule + data            (sa disponibilité, ses refs)
```

Chaque extension est indépendante. On peut avoir un agent avec juste
`name + role + type + fsm` ou un agent complet avec tout.

---

## 10. Résumé des décisions architecturales

| Décision | Justification |
|----------|---------------|
| Agent = automate à états | Le LLM n'est qu'un tool provider parmi d'autres |
| Occupation = continue/noncontinue/disparate | Pattern d'activité, pas moteur |
| Resources extensibles (llm, ram, cpu...) | Chaque ressource a son gestionnaire |
| Pas de provider dans la déclaration | Le LLM est un détail runtime, pas d'identité |
| Organisateur assigne les LLM | L'agent ne choisit pas, l'orga décide |
| Threads hydratés/déshydratés | OS gère le multithreading, Phénix |
| Signaux en canal parallèle | Supervision sans interruption |
| Agent Manager ≠ Ticker | Manager = agents, Ticker = infra |
| FSM Interpreter unique | Tous les agents passent par le même moteur |
| Service agents = agents normaux | Pas de traitement spécial, juste un rôle |
