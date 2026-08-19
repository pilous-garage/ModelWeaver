# Spec — local_catalogue (référentiel méta des données)

Statut : V1 à implémenter (design session 2026-08-17, carnet section R).

## 1. Principes

- Le catalogue local est un **référentiel méta** : données LÉGÈRES et typées,
  jamais de contenu lourd. Le contenu vit dans des FICHIERS référencés
  (`file`), le disque est la seule vérité pour le contenu.
- **VERSIONNÉ MULTI-SOURCES** : une data = un quadruplé
  `(namespace, name, source_id, version)` UNIQUE, chaque entrée a son propre
  `data_id` (hash stable du quadruplé). Les sources (user, official,
  enterprise, distant, friend, git_depot…) sont déclarées
  (`global_local_source` + mirroirs d'adresses `global_local_mirror_sources`).
- **ACCÈS PAR SÉLECTION** : tag (prioritaire) → préférence de source (ex.
  `user>enterprise>official`) → tri de version (newest/oldest/littérale).
  Syntaxe : `catalogue.<type>.<ns...>.<name>[:<source>@<version>][:tag(nom)]`.
- **GARDE D'ÉCRITURE** : un writer ne modifie que les lignes de SA source ;
  modifier une data d'une source étrangère crée une NOUVELLE entrée (sa
  source, nouvelle version) — jamais d'écrasement. Nettoyage : prune par
  source (keep=N).
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
| `global_local_source` | sources déclarées : sources_id, type_source (user/official/enterprise/distant/friend/git_depot), ref UNIQUE, active |
| `global_local_mirror_sources` | adresses (mirroirs) d'une source : (sources_id, address) UNIQUE |
| `global_local_shared_default` | partage par défaut (data_type, tag_type, tag_value) → can_be_shared (non/everyone/enterprise/friends/official) |
| `global_local_namespace` | arborescence ns (parent), tri/hiérarchie des data |
| `global_local_path` | chemins symboliques (path_name → address, scheme) |
| `global_local_meta` | versioning |
| `global_local_privilege` (+ `_condition`) | famille SECURITY (writer `write_catalogue_priv`) |
| `global_local_security_supervisor` | famille SECURITY (superviseurs désignés) |

Le tampon des opérations externes (`buffer_op`) vit dans le DOMAINE SÉPARÉ
`buffer` (buffer.db) — voir §5.

## 3. Modèle — tables PAR TYPE (DDL dynamique `create_type` ou seed)

Pour chaque type `x` (fichier, skill, agent, team, provider, model, …) :

```
x_data                — une ligne par ENTRÉE (quadruplé ns/name/source/version)
x_tag                 — tags : (data_id?, tag_type, tag_value) UNIQUE + CASCADE
x_tag_type            — registre des tags du type : tag_type PK, tag_value_type, description
x_source_and_sharing  — provenance/partage : data_id PK, source, source_url,
                        is_from_share, is_it_shared, can_be_shared, shared_at
```

**Règle sharing (rappel 2026-08-18)** : `is_it_shared`/`can_be_shared` sont
**toujours `false`/`'non'` par défaut**, pour TOUT (default ET partage
spécialisé `global_local_shared_default`). Aucun seed de partage ; le
**demandeur de sharing** (qui activera le partage à la demande) est prévu plus
tard. Valeurs par défaut déjà portées : schéma `DEFAULT 0`/`DEFAULT 'non'` +
`set_sharing(is_it_shared=False, can_be_shared="non")` +
`set_shared_default(can_be_shared="non")` + `resolve_can_be_shared → "non"`.

`x_data` — colonnes socle (V4 — modèle versionné multi-sources) :
```
data_id      INTEGER PRIMARY KEY   — hash stable(quadruplé) : un data_id PAR
                                     entrée (namespace, name, source, version)
name         TEXT NOT NULL
namespace    TEXT NOT NULL DEFAULT ''   (clé de global_local_namespace)
source_id    INTEGER NOT NULL           (→ global_local_source)
version      TEXT NOT NULL DEFAULT 'latest'
data_value_type TEXT                  — text[n], string, int, uint, float, bool, date,
                                       timestamp, json, file, row(...)
value        TEXT                        — scalaire ou json ; '{}' par défaut
ref_file     TEXT                        — adresse absolue du fichier (si file / ref)
path         TEXT                        — chemin symbolique (résolu via global_local_path)
description  TEXT
status       TEXT DEFAULT 'active'   — 'active' | 'missing' | 'archived'
last_modify  TEXT                    — posé par le writer à chaque modification (seule trace)
created_at / updated_at
+ colonnes dynamiques data_value_* (si data_value_type = row)
+ UNIQUE(namespace, name, source_id, version)
```

Chaque (source, version) a SA ligne : un import d'une nouvelle version ne
touche jamais la ligne d'une autre source — il crée la sienne (pas de conflit
entre sources pour un même numéro de version).

### Accès / sélection (V1)

Syntaxe : `catalogue.<type>.<ns1>.<ns2>.<name>[:<source>@<version>][:tag(<nom>)]`
(héritée : `<ns>/<name>[@version]`).

Ordre de résolution — **tag d'abord, puis source, puis version** :
1. filtre TAG : entrées portant le tag_type `nom` (n'importe quelle valeur) ;
2. SOURCE : `all` = toutes les sources actives ; `a>b>c` = ordre de
   préférence (première source qui a des lignes) ; source seule sinon.
   Défaut : `user>enterprise>official` ;
3. VERSION : `newest` (max numérique-aware : 1.10 > 1.9), `oldest`,
   ou version littérale. Défaut : `newest`.

Exemples : `catalogue.provider.models.openai.gpt4:official@1.2.0`,
`catalogue.skill.analyse:user>enterprise@newest:tag(stable)`.

### Écritures / garde de source

- add (ou upsert) : crée/met à jour la ligne du quadruplé de SA source.
- modify : si la ligne sélectionnée est de SA source → mise à jour EN PLACE
  (identité conservée) ; sinon → NOUVELLE entrée de sa source, avec la
  version fournie (ou la version suivante de sa source si non fournie).
- delete : interdit sur une ligne d'une autre source (WriteDenied).
- Le consumer du buffer applique chaque op avec la source portée par le
  payload de l'op (défaut 'user') — une op ne touche que les lignes de sa
  source.
- Nettoyage : `prune` (par famille/source, keep=N dernières versions) —
  TODO route.

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

## 5. Buffer (domaine SÉPARÉ — `buffer.db`, writer dédié `write_buffer`)

Table `buffer_op` :

```
op_id PK, direction 'in'|'out', domain (data_type), op 'add'|'modify'|'delete'|'refresh',
payload_json, status 'pending'|'applied'|'error'|'cancelled', error,
external_tag (source : models.dev, manual, community…), ref_external,
created_at, applied_at
```

- Domaine à part (modules/sqlite/buffer), UN SEUL writer : token
  `write_buffer` (dépôt pending + marquage des status). Un importeur externe
  (ex. script models.dev : ~2 900 modèles) dépose ses N ops en UNE grosse
  écriture `pending` (batch SQL) — jamais d'upsert individuel, et il ne
  connaît JAMAIS le domaine local. Toute écriture sur buffer.db passe par les
  fonctions du writer buffer (block des écritures brutes, token obligatoire).
- Le payload de chaque op porte le QUADRUPLE (namespace, name, source,
  version) de la data cible : c'est la source de l'op qui détermine les
  lignes qu'il peut toucher (jamais celles d'une autre source).
- **Vocabulaire** (modules/sqlite/buffer/write) :
  - `import_ops` (ailleurs → buffer, direction `in`) : `source`/`version`
    = défauts ; si le payload ne porte pas de version, elle est résolue par
    `resolve_version` — timestamp de la SOURCE (`last_updated` per entrée,
    sinon `release_date`), puis date du snapshot. Une source d'op manquante →
    ValueError.
  - `import_included` : variante STRICTE — passer None ⟺ la donnée porte
    elle-même source/version (requis, sinon erreur) ; passer une valeur ⟺ la
    donnée ne la porte pas → appliquée à toutes les ops. Pas de fallback
    "date du jour" en mode strict.
  - `import_local` (buffer → local) : le consumer, VIT DANS LE DOMAINE LOCAL
    (`local/write.import_local(buffer_w, local_w)`) : c'est le writer LOCAL
    qui applique les ops `pending` dans `x_data` en mini-batchs, via les
    fonctions dédiées du buffer (`mark_status`, `purge_applied`) — le local
    est exceptionnellement autorisé à marquer/supprimer dans buffer.db, mais
    JAMAIS en écriture brute.
  - `export_local` (local → buffer, direction `out`) : le writer local passe
    PAR le writer buffer pour déposer ses modifs à exporter — jamais en brut.
  - `export` / `export_included` (buffer → ailleurs) : consumer OUT — les ops
    `out` pending à envoyer. **source/version sont des ARGUMENTS de
    sélection** : on n'envoie QUE les correspondances (export_included :
    None = aucune contrainte) ; les payloads exportés GARDENT le quadruple
    (source/version dans les data). Après envoi réussi, le sender confirme
    via `mark_status(op_ids, 'applied')` (ou 'error').
- Les ops `applied` sont purgées en fin de cycle (audit court) ; requête par
  (external_tag, status) pour rejouer les erreurs (`buffer/read.status`,
  `buffer/write.retry`).

## 6. Domaines / familles (à déclarer dans domain_writers)

| Famille | Tables couvertes | Writer |
|---|---|---|
| local_data | x_data, x_tag, x_tag_type, x_source_and_sharing (+ sources) | write_catalogue |
| local_security | global_local_privilege(+condition), global_local_security_supervisor | write_catalogue_priv |
| local_env | global_local_namespace, global_local_path | write_catalogue |
| local_support | global_local_data_type, global_local_shared_default, global_local_meta | write_catalogue |
| buffer (domaine séparé buffer.db) | buffer_op | write_buffer (dépôt pending) ; consumer = writer local (write_catalogue) |

Principes hérités : écrivain unique par domaine, garde sur écriture, fail-safe
refus si domaine inconnu, bypass env (migration seule).

## 7. Routes daemon (catalogue_local/*)

- data_type : `add`, `delete`, `modify`, `get`, `list` (activate/deactivate)
- data : `add` (upsert), `delete`, `modify`, `get` (format plat row +
  sélecteurs source@version/tag), `list` {type, namespace, page, sort, order,
  status}, `list_versions`, `search` {q, tag_type, tag_value}, `refresh`
  (accessor, last_access)
- source : `add`, `list`, `add_mirror` (sources + mirroirs)
- tag_type : `add`, `delete`, `modify`, `get`, `list` (avec tag_value_type)
- tag : `attach`, `detach`, `list`, `by_value`, `types`
- sharing : rules `add/delete/modify/get` + `shared_default`
- buffer : `import_ops`/`import_included` (pending), `import_local` (consumer
  IN du writer local), `export`/`export_included`/`export_local` (consumer
  OUT), `status` par (external_tag, status), `retry`
- prune (nettoyage des vieilles versions par source, keep=N) : TODO route
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