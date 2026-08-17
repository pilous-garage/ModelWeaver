# Spec — local_catalogue (référentiel méta des données)

Statut : V1 à implémenter (design session 2026-08-17, carnet section R).

## 1. Principes

- Le catalogue local est un **référentiel méta** : données LÉGÈRES et typées,
  jamais de contenu lourd. Le contenu vit dans des FICHIERS référencés
  (`file`), le disque est la seule vérité pour le contenu.
- **Non-bloquant partout** : une donnée un peu périmée est acceptable.
  Lectures = jamais d'écriture (connexions mode=ro, ou SQLite direct href).
  Écritures = UN SEUL writer (token `write_catalogue`) **en mini-batchs**.
- Les lecteurs lourds (recréation de la table d'adresses, bridge, requêtes
  complexes) accèdent **directement** en SQLite ro — pas par le daemon.
- **source de vérité** : local_catalogue détient les données modifiables ;
  catalogue.db (cat. LLM) reste le référentiel dérivé (adresses, scores,
  budgets) recalculé depuis ici.

## 2. Modèle — tables FIXES (préfixe `global_local_`)

| Table | Rôle |
|---|---|
| `global_local_data_type` | registre des types : data_type_id, code UNIQUE, description, active |
| `global_local_shared_default` | partage par défaut (data_type, tag_type, tag_value) → can_be_shared (non/everyone/enterprise/friends/official) |
| `global_local_namespace` | arborescence ns (parent), tri/hiérarchie des data |
| `global_local_path` | chemins symboliques (path_name → address, scheme) |
| `global_local_meta` | versioning |
| `global_local_privilege` (+ `_condition`) | famille SECURITY (writer `write_catalogue_priv`) |
| `global_local_security_supervisor` | famille SECURITY (superviseurs désignés) |
| `global_local_buffer_op` | tampon entrée/sortie (voir §5) |

## 3. Modèle — tables PAR TYPE (DDL dynamique `create_type` ou seed)

Pour chaque type `x` (fichier, skill, agent, team, provider, model, …) :

```
x_data                — une ligne par data
x_tag                 — tags : (data_id?, tag_type, tag_value) UNIQUE + CASCADE
x_tag_type            — registre des tags du type : tag_type PK, tag_value_type, description
x_source_and_sharing  — provenance/partage : data_id PK, source, source_url,
                        is_from_share, is_it_shared, can_be_shared, shared_at
```

`x_data` — colonnes socle :
```
data_id      INTEGER UNIQUE      — hash stable(ref) par type (int64, type xxhash) :
                                   identique entre load/reload ; PAS d'AUTOINCREMENT
ref          TEXT UNIQUE         — utils/bubble_sort@v1
name         TEXT
namespace    TEXT (≠ '' → global_local_namespace)
version      TEXT DEFAULT 'latest'
data_value_type TEXT            — text[n], string, int, uint, float, bool, date,
                                  timestamp, json, file, row(...)
value        TEXT               — scalaire ou json ; '{}' par défaut ; PAS de contenu lourd
ref_file     TEXT               — adresse absolue du fichier (si file / ref)
path         TEXT               — chemin symbolique (résolu via global_local_path)
description  TEXT
status       TEXT DEFAULT 'active'   — 'active' | 'missing' | 'archived'
last_modify  TEXT               — posé par le writer à chaque modification (seule trace)
created_at / updated_at
+ colonnes dynamiques data_value_* (si data_value_type = row)
```

Couple unique matériel : (data_type_id, data_id) ; clés étrangères des tables
dérivées = (data_type_id, data_id) — plus de entry_id AUTOINCREMENT.

### data_value_type
- scalaires : `text[200ch]`, `string`, `int`, `uint`, `float`, `bool`, `date`,
  `timestamp`, `json` (extensibles plus tard)
- `file` : pure référence — `ref_file` obligatoire, `value` NON chargée ;
  l'utilisateur de la data charge le fichier lui-même
- `row(header1=type1, header2=type2, …)` : une LIGNE typée → colonnes
  matérielles `data_value_header1` (type type1) etc. Récursivité `row(row)`
  possible : `data_value_parent__row_header`. PAS de `row(colonne)`.
  Une data-table (plusieurs lignes) = type externe (file/blob), pas row.
  `get_data` renvoie le format plat `row: {header1: v1, …}` (format requête SQL).

### Tags
- Registre `x_tag_type` : chaque tag_type porte `tag_value_type`
  ∈ bool (binaire) | text | number | date | list (multivaleurs) | range (min/max)
  (extensible plus tard)
- Toutes les data préexistantes sont NON taggées (aucun tag au départ)
- `x_tag` : (data_id, tag_type, tag_value), UNIQUE, CASCADE sur data

## 4. Traces

- `last_access` : SUPPRIMÉ (il ne servait qu'au flush).
- `last_modify` : colonne sur `x_data`, posée par le writer seul.
- Les utilisateurs gardent leur propre date d'accès ; pour savoir si une data
  a changé → route `data_refresh(data_id, last_access)` : renvoie la data si
  `last_modify > last_access`, sinon « non modifié » (sémantique 304).

## 5. Buffer catalogue (`global_local_buffer_op`)

```
op_id PK, direction 'in'|'out', domain (data_type), op 'add'|'modify'|'delete'|'refresh',
payload_json, status 'pending'|'applied'|'error'|'cancelled', error,
external_tag (source : models.dev, manual, community…), ref_external,
created_at, applied_at
```

- Tout importeur (ex. script models.dev : ~2 900 modèles) dépose ses N ops en
  UNE grosse écriture `pending` (batch SQL) — jamais d'upsert individuel.
- Le **consumer du writer** local (toutes les ~1-2 s ou sur signal) applique
  le batch `pending` dans `x_data` en mini-batchs → `applied` (+ last_modify) ;
  les ops en `error` sont marquées, le batch continue (jamais de blocage).
- Modifs locales → direction `out` (occupé par l'export distant futur).
- Les ops `applied` sont conservées (audit) ; requête par
  (external_tag, status) pour rejouer les erreurs.

## 6. Domaines / familles (à déclarer dans domain_writers)

| Famille | Tables couvertes | Writer |
|---|---|---|
| local_data | x_data, x_tag, x_tag_type, x_source_and_sharing | write_catalogue |
| local_security | global_local_privilege(+condition), global_local_security_supervisor | write_catalogue_priv |
| local_env | global_local_namespace, global_local_path | write_catalogue |
| local_support | global_local_data_type, global_local_shared_default, global_local_meta | write_catalogue |
| local_buffer | global_local_buffer_op | write_catalogue (+ importeurs externes : dépôt pending uniquement) |

Principes hérités : écrivain unique par domaine, garde sur écriture, fail-safe
refus si domaine inconnu, bypass env (migration seule).

## 7. Routes daemon (catalogue_local/*)

- data_type : `add`, `delete`, `modify`, `get`, `list` (activate/deactivate)
- data : `add` (upsert), `delete`, `modify`, `get` (format plat row),
  `list` {type, namespace, page, sort, order, status}, `search` {q, tag_type,
  tag_value}, `refresh` (data_id, last_access)
- tag_type : `add`, `delete`, `modify`, `get`, `list` (avec tag_value_type)
- tag : `attach`, `detach`, `list`, `by_value`, `types`
- sharing : rules `add/delete/modify/get` + `shared_default`
- buffer : `push(pending)`, `process()` (consumer), `status` par (external_tag, status)
- PAS de SQL exposé par le daemon : les process lourds passent en SQLite ro direct.

## 8. Types connus (seed V1)

| data_type | value | Remplissage |
|---|---|---|
| fichier | file (hash) | file_watcher (existant) |
| skill | file | import AgentsCatalogue/skills (196, tri plus tard) |
| agent | file | import des 11 agents (voir §9) |
| team | file | import dev-chat + llm-code |
| provider / model / endpoint | row(...) | script models.dev → buffer |

Pas de `large_text`/`large_blob`/`bench`/`agent_inline` en V1 (idées reportées,
carnet R2).

## 9. Inventaire de remplissage (agents/skills/teams)

- Agents (11) : chat-pilot, as-llm-leader, greedy-coder, greedy-consensus,
  greedy-decoupeur, greedy-explore, greedy-merger, greedy-prepare-response,
  greedy-reviewer, greedy-tester, proxy_llm_fallback
- Teams (2) : dev-chat, llm-code
- Skills : les 196 (import complet, filtre obsolètes plus tard)
- SUPPRIMÉS (fichiers teams retirés, 2026-08-17) : build, example-team,
  swarm-selfimprove, swarm-selfimprove-v2, gui-tasks, bug-busters
  (agents inline = mauvaise pratique ; à redéfinir si besoin)
- Ignorés agents : *.old, @v2 non référencés, test_*, agent_default,
  sub_agent_default, answering_machine, manager_de_docker

## 10. Migration

RESET complet : les tables locales actuelles sont vidées/recréées au nouveau
schéma (NOT migration par renommage). Re-remplissage par les scripts d'import
(§9) + re-seed des privileges par défaut (catalogue_privileges_defaults).