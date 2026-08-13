# Spécification consolidée — Taskflow : task / sub_task / supervisor

> Statut : conception (2026-08-13). Refonte du système d'attribution et de
> transformation des tâches du swarm. Ce document est la référence pour
> l'implémentation.

---

## 1. Principes fondateurs

1. **Le workflow n'est PAS porté par des transitions de tâche** (coding → review).
   Il est porté par la **création de nouvelles sub_tasks** + une chaîne de
   **dépendances**. Un `coding done` ne devient jamais `review` : on **crée** une
   nouvelle sub_task `review`, et le `coding` passe `supervised`.
2. **Deux niveaux de modélisation** :
   - `task` = le **sujet** suivi (feature, chat, completion…) ;
   - `sub_task` = un **passage de relais** sur ce sujet (analysis, coding,
     testing, review, merge, respond…). **Une ligne par sub_task**.
3. **La découpe génère toujours un merge automatique** : découper A en B et C
   produit B, C, **et** `merge(B,C)`. Le merge est un noeud intermédiaire créé
   par le skill de découpe, jamais déclaré explicitement par l'analyste.
4. **Représentation en DAG** : B et C sont les parents (amont), le noeud
   découpé A est l'enfant (aval) — **A nécessite B et C**, donc `A` dépend de
   `merge(B,C)`, qui dépend de B et C.
5. **L'analyste produit toujours un `analysis-report`** (plus ou moins gros),
   nommé avec le nom de la tâche analysée, déposé dans un dossier commun. Cela
   simplifie l'analyse post-merge.
6. **Le task_supervisor est généraliste et sans LLM** : il applique le même
   système à toutes les team, mais peut être **précisé par un tableau de règles**
   `(task_type, tag) → (task_type, tag)`.

---

## 2. États d'une sub_task

Chaque sub_task progresse **linéairement, une seule fois** :

```
waiting_dependencies → unattributed → doing → done / cancelled → supervised
```

| État | Signification |
|---|---|
| `waiting_dependencies` | Noeud **découpé** qui attend ses parents (B, C ou `merge(B,C)`). Toujours **avant** `unattributed`. Posé par le découpeur. |
| `unattributed` | Dans la pool, personne ne l'a prise. |
| `doing` | Un agent l'a prise. |
| `done` / `cancelled` | Livré / abandonné par l'agent, **+ tag** posé par lui. |
| `supervised` | **Terminal** : le task_supervisor a clos le pipeline du groupe complet (tâche + descendants). |

Règle : `waiting_dependencies` **précède** toujours `unattributed`. Une tâche
découpée ne redevient traitable que lorsque ses dépendances sont résolues.

---

## 3. Niveaux (task / sub_task)

- **`tasks`** : le sujet. `task_id`, `task_type` (ex. `chat_entry`,
  `completion_entry`…), priorité, statut global dérivé, tag.
- **`sub_tasks`** : le relais. `sub_task_id`, `task_id`, `sub_task_type`
  (`analysis`, `coding`, `testing`, `review`, `merge`, `respond`…), statut,
  tag.

### Types de sub_task (noyau)
- `analysis` — découpe / analyse (agent analyste, LLM).
- `coding` — production (agent codeur, LLM).
- `testing` — tests (agent testeur, LLM).
- `review` — relecture/validation (agent reviewer, LLM). **Des agents reviewer
  existent et piochent les sub_tasks de type `review`.**
- `merge` — fusion des livrables d'une découpe (agent mergeur, LLM).
- `respond` — réponse finale de l'exitpoint (agent prepare-response).
- `exploration` — devient un **sous-agent** (hors relais principal).

### Le circuit et le court-circuit
Le circuit `analysis → coding → testing → review → merge` n'est **ni
automatique ni complet** : la chaîne de dépendances le définit. Chaque
sous-tâche de la découpe déclare **les niveaux de nécessité** qui la
concernent. Exemples : s'arrêter au coding (review globale en fin de groupe),
faire review sans testing, etc. Une sub_task terminale n'a pas de suivant ;
le supervisor ne crée rien de plus.

---

## 4. Dépendances

Table `task_dependencies` :

| colonne | rôle |
|---|---|
| `child_id` | la sub_task aval qui attend |
| `parent_id` | la sub_task amont requise |
| `required_state` | état requis du parent (défaut `done`) |
| `required_tag` | **tag requis** du parent (ex. `merge/ok`, `done/ok`) — précise l'étape attendue |

Chaque dépendance précise **quelle étape** et **quel résultat** on attend.
Le `waiting_dependencies` d'un noeud se lève (→ `unattributed`) quand **toutes**
ses dépendances sont satisfaites (état + tag).

---

## 5. Découpe (A → B + C)

Le skill de découpe de l'analyste (`analysis/decoupe@v1`) prend :
- la tâche/sub_task analysée ;
- la liste des sous-tâches avec **niveau de nécessité** par type
  (`analysis`, `coding`, `testing`, `review`…).

Il **génère automatiquement** :
```
          B ──┐
              ├──► merge(B,C) ──► A (child, nécessite merge(B,C))
          C ──┘
```
- B, C naissent avec leurs dépendances internes et leurs niveaux.
- `merge(B,C)` est créé (type `merge`), **dépend de B et C**.
- **A passe en `waiting_dependencies`**, dépendant de `merge(B,C)`.
- A ne redevient `unattributed` que quand `merge(B,C)` est `done` (+ tag).

L'analyste **produit toujours un `analysis-report`** (contenu, dossier commun,
nom contenant le nom de la tâche analysée).

---

## 6. Le merge

L'agent mergeur pioche la sub_task `merge` :
1. **try simple merge** : fusion git automatique (les merges sans conflit sont
   bien gérés par git) ;
2. **resolve conflict** : si conflit, résolution (LLM) avant de clôturer.

Le merge pose le tag résultat (`merge/ok`, `merge/conflict`…).

---

## 7. Le task_supervisor

**Agent sans LLM, un par team, généraliste.**

### Rôles
1. **Supervision** : pour toute sub_task de la team en
   `done` / `cancelled` / `waiting_dependencies` non supervisée :
   - applique la **règle** `(task_type, tag) → (task_type, tag)` :
     crée la sub_task suivante (avec dépendance) ;
   - **groupe complet** (tâche + descendants) clos → `supervised`.
2. **Attribution** : traite la file `ask_new_task` :
   - attribue une sub_task `unattributed` dispo (mapping type↔rôle, niveau
     max, priorité, âge) ;
   - sinon répond "en attente".
3. **Réveil** : **déshydrate** l'agent ciblé quand une sub_task devient
   disponible pour lui. Le supervisor **a le contrôle** du réveil.

### Tableau de règles
Le supervisor est généraliste par défaut, **précisable** par une suite de
règles `(task_type, tag) → (task_type, tag)`. Exemples :

| (type, tag) | → crée |
|---|---|
| `(coding, done/ok)` | `review` ou `testing` (selon nécessité) |
| `(testing, ok)` | `merge` |
| `(merge, ok)` | suite du groupe / `respond` |
| `(coding, done/failure)` | `analysis` (re-découpe) |
| `(testing, fail)` | `coding` (fix) |
| `(review, fail)` | `coding` (correction) |

Le supervisor lit la règle qui matche `(type, tag)` de la sub_task clôturée.

### Tick = failsafe, léger
Le tick du supervisor est un **failsafe**, pas le chemin nominal :
- chemin nominal : l'agent fait `ask_new_task` **synchrone** (cf. §8) ;
- tick : réveille les agents "en attente" quand une sub_task devient dispo en
  dehors d'un ask (cas passif). Il tourne à un rythme lent (pas seconde).

---

## 8. Cycle d'un agent greedy (`ask_new_task` remplace `sleep` + `pick`)

Au début de chaque run :

```
ask_new_task:
  1. sub_tasks déjà ATTRIBUÉES à moi ?
       (faux départ / relaunch / plantage / attribution directe du supervisor)
       → plusieurs → je prends la plus VIEILLE avec la plus HAUTE priorité
       → une seule → je la prends (doing)
  2. Aucune sub_task attribuée (cas normal greedy)
       → écrire la demande en BDD (file ask_new_task)
       → CALL BLOQUANT au supervisor (synchrone, sans passer par le tick)
       → réponse :
           • attribution → je prends la sub_task (doing)
           • "en attente" → déshydratation (vrai pause)
```

- La demande porte : `agent_id` + liste `(task_type, level_max)` (un agent peut
  gérer plusieurs types).
- Le **supervisor choisit qui réveiller** quand une sub_task devient dispo.

---

## 9. Discussion synchrone vs tick

- **Synchrone (chemin nominal)** : le skill `task_ask_new` écrit la demande en
  BDD (traçabilité + file de secours) puis **appelle le service supervisor en
  bloquant** jusqu'à la réponse. Pas de tick pour la réponse.
- **Tick (failsafe)** : le supervisor réveille les agents "en attente" quand
  une sub_task dispo apparaît (réveil = **déshydratation** de l'agent ciblé).
  L'agent désydraté refait `ask_new_task` → step 1 (attribution directe).

---

## 10. Waker et ticker global

### Waker par team (fusionné au supervisor)
Le supervisor par team fait : **supervision + attribution + réveil**. Il
remplace le picker et le waker "tâche".

### Waker généraliste (hors team)
Gère les **réveils hors tâche** : horaires, nombre d'erreurs loggées,
événements/conditions.

### Ticker global (léger)
Un seul ticker central qui exécute une **liste de jobs** contre l'état BDD à
intervalle régulier. **Polling d'état réel** (pas d'événements → aucune
modification fantôme non récupérée).

Chaque job utilise des **requêtes SQL ciblées indexées** (jamais de scan de
toute la table) :
- supervision : `WHERE team_id=? AND status IN ('done','cancelled',
  'waiting_dependencies') AND supervised=0 LIMIT n` — index
  `(team_id, status, supervised)` ;
- attribution : file `ask_new_task` non servie (petite table).

---

## 11. Schéma BDD

```sql
-- Le sujet
CREATE TABLE tasks (
    task_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL,
    description    TEXT DEFAULT '',
    task_type      TEXT DEFAULT 'feature',      -- chat_entry, completion_entry…
    priority       INTEGER DEFAULT 0,           -- numérique, + haut = + prioritaire
    workspace_id   INTEGER, team_id INTEGER,
    status         TEXT DEFAULT 'unattributed', -- état dérivé global
    tag            TEXT DEFAULT '',             -- tag terminal du sujet
    repo TEXT DEFAULT '', branch TEXT DEFAULT '',
    primordial INTEGER DEFAULT 1,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(workspace_id, status, priority);

-- Le relais (une ligne par étape)
CREATE TABLE sub_tasks (
    sub_task_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id        INTEGER NOT NULL REFERENCES tasks(task_id),
    sub_task_type  TEXT NOT NULL,               -- analysis, coding, testing, review, merge, respond
    status         TEXT DEFAULT 'unattributed', -- waiting_dependencies | unattributed | doing | done | cancelled | supervised
    tag            TEXT DEFAULT '',             -- posé par l'agent d'exécution, contraint par type
    difficulty     TEXT DEFAULT 'medium',
    assigned_to    TEXT DEFAULT '',
    freedby        TEXT DEFAULT '',
    supervised     INTEGER DEFAULT 0,
    repo TEXT DEFAULT '', branch TEXT DEFAULT '',
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_subtasks_team_status
    ON sub_tasks(workspace_id, status, supervised, sub_task_type);
CREATE INDEX IF NOT EXISTS idx_subtasks_assigned ON sub_tasks(assigned_to, status);

-- Dépendances entre sub_tasks
CREATE TABLE task_dependencies (
    child_id       INTEGER NOT NULL,
    parent_id      INTEGER NOT NULL,
    required_state TEXT DEFAULT 'done',
    required_tag   TEXT DEFAULT '',             -- étape + résultat attendus
    PRIMARY KEY (child_id, parent_id)
);

-- File d'attribution
CREATE TABLE ask_new_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   INTEGER NOT NULL,
    types      TEXT DEFAULT '',                 -- JSON: [{type, level_max}]
    status     TEXT DEFAULT 'pending',          -- pending | served | answered_wait
    requested_at TEXT, served_at TEXT
);
```

> NB : schéma d'intention. Les colonnes exactes seront alignées sur le schéma
> workspace existant lors de l'implémentation (migration).

---

## 12. Acteurs (récapitulatif)

| Acteur | LLM | Rôle |
|---|---|---|
| `as_llm_leader` | **non** | Entrypoint/exitpoint du swarm_as_llm : crée la tâche d'entrée (`chat_entry`, `completion_entry`…) ; reçoit la sub_task `respond` (txt + tool_calls + `.json`) et clôt. |
| `pilote_chat` | oui | Dialogue utilisateur (porteur de LLM côté entrée). |
| `analyste` (`analysis-planning`) | oui | Pioche les sub_tasks `analysis`, produit l'`analysis-report`, découpe (skill `analysis/decoupe@v1`). |
| `codeur`, `testeur`, `reviewer`, `mergeur`, `prepare-response` | oui | Prement les sub_tasks de leur type ; `done`/`cancelled` + tag. |
| `task_supervisor` | **non** | Un par team : supervision, attribution, réveil (déshydratation). Règles `(type,tag)→(type,tag)` configurables. |

---

## 13. Tags (posés par l'agent d'exécution, contraints par type)

| sub_task_type | tags possibles |
|---|---|
| `analysis` | `ok`, `failure`, `need_split` |
| `coding` | `done/ok`, `done/failure`, `done/need_split` |
| `testing` | `ok`, `fail`, `no_test` |
| `review` | `ok`, `fail` |
| `merge` | `ok`, `conflict` |
| `respond` | `ok` |

Le tag est le **signal de transition** lu par le supervisor et la **trace de
certification** du workflow (chaîne de tags = historique).

---

## 14. Séquence de bout en bout (exemple)

1. `as_llm_leader` crée la task `T` (`chat_entry` / `completion_entry`).
2. Le supervisor crée une sub_task `analysis` pour T (ou le leader le fait
   directement) → `unattributed`.
3. L'analyste pioche `analysis` (via `ask_new_task`), produit l'`analysis-report`
   et découpe T en B et C (niveaux de nécessité). Le skill génère B, C et
   `merge(B,C)` ; T passe `waiting_dependencies` de `merge(B,C)`.
4. B, C, `merge` deviennent `unattributed` selon leurs dépendances ; les agents
   (codeur/testeur/reviewer) les prennent, les font, posent leurs tags.
5. `merge(B,C)` (dépend de B et C) fusionne (try simple merge → resolve
   conflict). Tag `merge/ok`.
6. La dépendance de T est résolue → T passe `unattributed` → suite du circuit
   de T (re-analysis post-merge, coding…).
7. Groupe complet clos → le supervisor passe tout `supervised`.
8. Le supervisor crée la sub_task `respond` → `prepare-response` la prépare
   (txt + tool_calls + `.json`) → `as_llm_leader` (exitpoint) la remonte à
   l'appelant.

---

## 15. Décisions d'implémentation (prochaines étapes)

- [ ] Migration BDD : séparer `tasks` / `sub_tasks`, ajouter `tag`,
      `required_tag`, file `ask_new_task`, index.
- [ ] Skill `analysis/decoupe@v1` (génère B, C, merge ; analysis-report).
- [ ] Skill `task_ask_new` (step 1 + file BDD + call synchrone au supervisor).
- [ ] Service `task_supervisor` (règles, attribution, réveil, tick failsafe).
- [ ] Suppression du picker / `claim_next` / `pick_token` au profit de
      l'attribution par le supervisor.
- [ ] `sleep` greedy → `ask_new_task` ; retrait du flux review auto du codeur
      (`approve=true` / statut `review` dans `done()`).
- [ ] Agents : `as_llm_leader` (sans LLM), `prepare-response`, reviewer
      conservé, exploration → sous-agent.
- [ ] Ticker global léger (jobs indexés).
