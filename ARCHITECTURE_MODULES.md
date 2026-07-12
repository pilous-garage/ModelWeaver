# ModelWeaver — Architecture modulaire & Hard-check des contrats

> Statut : **prototype en cours**. Ce document définit la convention de découpage
> en `modules/` et `services/`, le format des contrats, et l'outil de vérification
> (`hardcheck`). Complète `ARCHITECTURE_API.md`.

---

## 1. Principes

- **Module** = brique de *logique* (bibliothèque, appelée en direct dans le process).
- **Service** = *processus* long avec cycle de vie (daemon, worker, watcher),
  qui communique via une API/IPC.
- Chaque unité (module **ou** service) déclare **deux contrats** :
  - ce qu'elle **expose** (surface publique),
  - ce qu'elle **consomme** (dépendances externes).
- **Règle d'or** : on n'importe jamais l'intérieur (`_impl`) d'une autre unité —
  uniquement sa surface publique déclarée. C'est ce qui rend le debug facile et
  empêche le couplage caché.

```
modules/<nom>/                     services/<nom>/
  __init__.py   (ré-exporte)         __init__.py
  <impl>.py     (logique interne)     service.py    (boucle/lifecycle)
  _contract/                          _contract/
    interface.py                        interface.py     (routes/opérations exposées)
    dependencies.py                     dependencies.py  (unités consommées)
    manifest.json (optionnel)           manifest.json    (type, démarrage, port…)
```

## 2. Le dossier `_contract/`

Choix de nom : **`_contract/`** (préfixe `_` → trié en tête, langage-neutre,
capture les deux sens : ce qu'on expose ET ce qu'on consomme).

### `_contract/interface.py` — surface publique
Pour un **module** :
```python
KIND = "module"
NAME = "checker"
# Symboles importables depuis le package (doivent exister et être exportés).
EXPORTS = ["Checker"]
```
Pour un **service** (contrat multi-langage = source de vérité des routes) :
```python
KIND = "service"
NAME = "api"
# La table de routes réellement servie, désignée par "module:attribut".
ROUTES_SOURCE = "daemon:ROUTES"
# Routes attendues -> paramètres attendus ([] si aucun).
EXPOSES = {
    "system/info": [],
    "jobs/add": ["ref", "job_type"],
    ...
}
```

### `_contract/dependencies.py` — dépendances consommées
```python
# unité source -> symboles consommés
CONSUMES = {
    "gui_helper": ["install_tool", "uninstall_tool", "_enqueue_job", ...],
}
```

## 3. L'outil `hardcheck/verify.py`

Vérification **statique + introspection** (pas une preuve absolue, mais un contrôle
entrée/sortie réel), exécutable en pré-commit / CI. Pour chaque unité :

1. **Exports résolvent** : chaque symbole de `EXPORTS` est importable depuis le
   package ; chaque route d'`EXPOSES` correspond à une entrée de `ROUTES_SOURCE`
   (et réciproquement — pas de route servie non déclarée, ni déclarée non servie).
   → *C'est le hard-check schéma ↔ implémentation.*
2. **Dépendances existent** : chaque symbole de `CONSUMES` existe réellement dans
   l'unité source (introspection).
3. **Frontières respectées** (`ast`) : en parsant les `.py` de l'unité, tout accès
   à une autre unité passe par sa surface publique ; toute dépendance réellement
   utilisée est déclarée dans `CONSUMES` (pas d'import « sauvage »).

Sortie : rapport `PASS/FAIL` par unité + code de sortie non nul si échec.

### Couverture multi-langage
- **Python ↔ Python** (modules/services) : couvert par ce `verify.py` (introspection + `ast`), renforçable par `mypy` + `Protocol`.
- **Python ↔ TS ↔ Rust** : couvert par **une seule couture** = l'API du service,
  décrite par `EXPOSES`. Le SDK TS (`mwClient.ts`) sera généré/validé contre ce
  même contrat → dérive = erreur de compilation. (étape suivante)

## 4. Migration (incrémentale)

1. ✅ Convention + `hardcheck` + **1ʳᵉ unité prouvée** = `services/api/`.
2. Migrer les modules purs un par un (`checker`, `installer`, `catalogue`…),
   en consolidant `projetclient/modules/*` vers `modules/*` (même chemin d'import
   `modules.X` → churn minimal).
3. Migrer les services (`catalogue`, `installer-worker`, `tester`, watchers) vers
   `services/*`, pilotés par le superviseur.
4. Brancher le hard-check en pré-commit + CI.
5. Générer le SDK TS depuis les `EXPOSES` (contrat multi-langage).
