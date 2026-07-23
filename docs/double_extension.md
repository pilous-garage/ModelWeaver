# Convention d'extension multiple pour les fichiers YAML

## Principe

Tous les fichiers du catalogue ModelWeaver utilisent `.yaml` comme **extension finale**.  
On ajoute une ou plusieurs extensions intermédiaires pour typer le contenu au premier coup d'œil
et permettre un filtrage automatique (éditeur, git, Tauri file-filter, etc.).

**Syntaxe :** `nom.{type}.yaml`  
Avec extensions optionnelles supplémentaires : `nom.{format}.{type}.yaml`

## Extensions par type d'objet

| Objet         | Extension                          | Exemple                              |
|---------------|------------------------------------|--------------------------------------|
| Agent         | `.agent.yaml`                      | `worker.agent.yaml`                  |
| Skill         | `.skill.yaml`                      | `run_shell.skill.yaml`               |
| Behavior      | `.behavior.yaml`                   | `analyse.behavior.yaml`              |

### Précision — Behavior

Un **behavior** est un **squelette de workflow pré-rempli** (structure de steps :
set_variable, call, switch, while…) avec les appels aux skills laissés **vides**
(`fn: ~` ou `fn: null`). Ce n'est pas un workflow exécutable en l'état.

Utilisation prévue (jeu lego) :
1. L'utilisateur pioche un behavior (ex. `analyse`) → il obtient la structure (read → llm → write).
2. Il glisse-dépose des skills dans les slots `fn: ~` pour les remplir.
3. Il sauvegarde → ça devient un agent ou un workflow standalone.

On pourra aussi **extraire un behavior d'un agent existant** (on garde la structure,
on vide les `fn` et les `uses_llm`, on retire les `capture` spécifiques).

Et à terme, créer un agent en partant d'un behavior vide :
1. Sélectionner le behavior → squelette dans l'IDE graphe
2. Glisser les skills depuis le catalogue
3. Assigner role + personality → agent complet
| Personality   | `.personality.yaml`                | `neutre.personality.yaml`             |
| Role          | `.role.yaml`                       | `architecte.role.yaml`               |
| Recipe        | `.recipe.yaml`   (BDD uniquement)  | `onboard.recipe.yaml`                |

## Extensions bonus de format (3ᵉ position)

Quand un même type d'objet peut être représenté dans un format dérivé,
on rajoute une extension avant le type :

| Format         | Préfixe   | Exemple                                         |
|----------------|-----------|-------------------------------------------------|
| Graph (workflow visuel) | `.graph`  | `worker.graph.agent.yaml`                        |
| Inline (compact généré) | `.inline` | `worker.inline.agent.yaml`                       |

**Règle :** `.yaml` toujours en dernière position.  
Le **type** (agent, skill, etc.) en avant-dernière.  
Le **format** (graph, inline) avant le type, optionnel.

## Cas en base de données

- Les `recipe` sont stockés en base (PostgreSQL), pas dans `AgentsCatalogue/`.
  Leur extension est `.recipe.yaml` mais ils sont nominalement gérés par
  l'API catalogue sous `catalogue/recipes/…`, pas par le filesystem.
- Autres types exclusivement BDD : `instance`, `run`, `project`, `todo`.
  Pas de fichier YAML direct pour ces types actuellement.

## Compatibilité

- Linux / macOS : double/triple extension 100 % supporté
- Windows : également supporté (NTFS, ReFS)
- Docker / web : aucun souci connu
- Seul risque : un vieux filesystem qui limiterait à 1 point d'extension.
  Non avéré avec nos déploiements actuels.

## Migration

Renommage une fois décidé :
```bash
cd AgentsCatalogue
for f in agents/*.yaml; do mv "$f" "${f%.yaml}.agent.yaml"; done
for f in skills/**/*.yaml; do mv "$f" "${f%.yaml}.skill.yaml"; done
# etc.
```
Puis mise à jour des règles de `Glob()` dans le backend (`services/api/catalogue_api.py`,
`services/role_manager.py`, `modules/catalogue/*.py`).