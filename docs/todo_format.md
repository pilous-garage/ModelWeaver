# TODO Format — Spécification du fichier todo.txt

Le fichier `todo.txt` (dans l'espace projet partagé) contient une tâche par ligne.

## Format

```
[status][type]{description}[files]
```

| Champ        | Requis | Valeurs                          | Description                  |
|-------------|--------|----------------------------------|------------------------------|
| `status`    | oui    | `pending`, `done`, `blocked`     | état de la tâche             |
| `type`      | oui    | `code`, `analyse`, `test`        | catégorie de travail         |
| `description` | oui  | texte libre                      | description de la tâche      |
| `files`     | non    | chemins séparés par `;`          | fichiers concernés           |

## Contrainte

- Une seule tâche par ligne (pas de multi-ligne).
- Pas d'espace entre les blocs.
- Le bloc `files` peut être omis (crochets vides `[]` ou entièrement absent).

## Exemples

```
[pending][code]{Implement login page}[src/auth/login.tsx]
[done][analyse]{Review API endpoints}[]
[blocked][test]{Add unit tests}[src/parser.test.ts;src/utils.test.ts]
[pending][code]{Refactor database layer}[src/db/]
[pending][analyse]{Benchmark rendering performance}
```

## Parsing

Les outils de parsing utilisent `sed` ou `awk` pour extraire le premier champ :

```bash
head -1 todo.txt | sed -n 's/^\[\([^]]*\)\]\[\([^]]*\)\]{\([^}]*\)}\[\([^]]*\)\]$/{\n"status":"\1",\n"type":"\2",\n"desc":"\3",\n"files":"\4"\n}/p'
```
