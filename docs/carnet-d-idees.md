# Carnet d'idées — fonctionnalités futures (non implémentées)

Idées notées au fil des sessions. Rien ici n'est implémenté — c'est la
réserve d'architecture pour les prochains chantiers.

---

## Idée 1 — Budgets tri-dimensionnels (thinking_power / money / token_volume)

**Statut** : concept validé, NON implémenté.

### Le problème
Choisir un LLM pour un `llm_call` au doigt mouillé ignore la réalité :
un oss-20B n'a pas la même valeur qu'un claude dernière génération, un
agent explore consomme beaucoup de tokens mais peu de réflexion, et la
plupart des utilisateurs sont limités par l'ARGENT, pas par la puissance.

### Les 3 dimensions (un budget peut être plafonné sur chacune)
1. **thinking_power** (Cog) — l'intensité cognitive du modèle, PAS le token
   count. Un modèle Reasoning (o1/o3/R1) = ~100, un standard = ~50, un petit
   rapide = ~10. Un OSS-20B n'a pas la valeur cognitive d'un claude.
2. **money** ($) — le disjoncteur financier absolu (max_money_usd par
   tâche/agent). Le free tier = 0.
3. **token_volume** (Vol) — la bande passante brute (input+output). Un agent
   RAG/explore/lecture de repo en consomme beaucoup sans réfléchir.

### Les ratios d'efficience (le ROI de l'intelligence)
- **thinking_power / money** : intelligence par dollar — optimise le coût
  (ex: DeepSeek-R1, modèles distillés locaux).
- **thinking_power / token** : densité cognitive (intelligence par mot) —
  crucial en contexte serré.
- **token / money** : bande passante économique (volume par dollar) — évident,
  compte pour le RAG/explore. Free tiers et locaux excellent ici.

⚠️ **thinking_power/money et thinking_power/token sont LES valeurs à générer**
pour chaque modèle — c'est le vrai défi (pas token/money, simple division
tarifaire).

### Les seuils minimaux par benchmark (la barrière de compétence)
Les ratios seuls sont dangereux : un vieux 3B gratuit aurait d'excellents
ratios mais échouerait à coder. Avant d'optimiser, on FILTRE par exigences
minimales :
```yaml
cognitive_requirements:
  benchmarks:
    min_swe_bench: 25.0
    min_human_eval: 85.0
    min_gsm8k: 90.0
  preferred_strategy: "maximize(thinking_power/money)"
```

### L'algorithme de routage (direct_bridge) en 3 passes
```
[Catalogue modèles dispo]
  │  Passe 1 : Filtre Hard    — exclut sous les seuils de benchmark
  │  Passe 2 : Filtre Budget  — exclut si coût estimé > max_money restant
  │  Passe 3 : Optimisation   — trie selon la stratégie (ex: max TP/$)
  ▼
[Modèle élu]
```

### Comment générer thinking_power (auto-calibration locale)
- Pas une valeur fixe/marketing : un **vecteur matriciel par compétence**
  (coding, logic, extraction, creative), indexé sur les types de tâches des
  agents.
- `thinking_power_matrix[capability]` extrait la valeur TP du modèle pour le
  `llm_call` concerné (la FSM déclare `capability`).
- **Densité cognitive réelle** : `TP_matrix[capability] / (input+output tokens)`
  enregistrée après chaque exécution réussie.
- **Cas des modèles Reasoning** : extraire les `reasoning_tokens` (balises
  `<think>`/API) pour calculer densité Brute vs Nette.
- **Money/TP réel** : moving average du coût réel facturé / TP délivré →
  `model_efficiencies` en SQLite, mise à jour à chaque tâche réussie.
- **Cold start** : valeurs par défaut (benchmarks publics), puis
  auto-calibration locale au fil de l'usage (le swarm réoriente vers le
  meilleur rapport effectif, ex. un llama-3 local qui devient infini en TP/$).

### Pondérer le LOCAL (il n'est ni gratuit ni infini)
- **Coût virtuel du local** : remplacer le "0$" par un coût d'infrastructure
  (électricité + amortissement GPU) — ex. 0.08$/M input, 0.15$/M output.
  Garde les divisions saines.
- **Friction temporelle** : `temps_réponse / tokens_générés` — un local à
  0.5 tok/s bloque le swarm ; le ratio chute dans la BDD → le bridge bascule
  sur une API à 1 centime plutôt que de paralyser 5 min.
- **Finitude / concurrence (slots VRAM)** : chaque modèle local actif
  consomme des slots. Cascade de routage matériel :
  - deadline lointaine → l'agent attend qu'un slot se libère (pause)
  - tâche urgente → contourne le local saturé, paie une API (coût compensé
    par le gain de temps)

### Intégration proposée
```yaml
# tâche ou agent
resource_budget:
  max_money_usd: 0.50
  max_token_volume: 500000
  min_thinking_power: 75
```

### Ce que ça rend possible
- **Sélecteur de modèle multicritère** dans direct_bridge (au lieu du modèle
  fixe ou du coût brut)
- **Anti-famine financière** : si budget money épuisé → l'agent suspend la
  tâche (statut PAUSED_OUT_OF_BUDGET), l'humain remet des centimes, on
  reprend (réutilise le mécanisme d'interruption/reprise)
- **Émergence** : un nouveau modèle open-source excellent/cher pas cher
  prend automatiquement la tête des agents de dev sans changer le code
- **Mini-OS** : allocation de ressources réelles (VRAM, temps, électricité)
  face aux contraintes économiques et de deadline
- **Transparence** : afficher les 3 jauges (vecteurs) dans la GUI en temps
  réel

### Notes
- On a une partie de la matière : `model_efficacy` (scores benchmark),
  `model_call_log` (tokens/succès), le scoring d'allocation existant, les
  `USE_CASE_REQUIREMENTS` (features) et `provider_models.agentic`.
- L'auto-test local d'un modèle Ollama (micro-benchmark embarqué) pour
  mesurer la compétence réelle est envisageable.

---

## Idée 2 — Redéfinir `ask_question_humain`

**Statut** : idée notée, NON implémentée.

Redéfinir la skill de question à l'humain avec 2 paramètres clés :
- `multichoice` : la question propose plusieurs choix (réponse structurée,
  pas un texte libre) — utile pour les autorisations, les arbitrages, les
  décisions rapides
- `time_before_working_recommended` : délai recommandé avant que l'agent
  reprenne le travail s'il n'y a pas de réponse (ex. 30s, 5min, 30min) —
  permet à l'agent de continuer en mode "best effort" plutôt que de
  bloquer indéfiniment sur une question sans réponse

Contexte : s'insère dans la cascade d'autorisation (membre → team_leader →
global_security → humain). Quand on escalade à l'humain, il faut poser une
question claire (multichoice) ET savoir quand reprendre le travail si
l'humain est absent.

---

## Idée 3 — Catalogue multi-étages (local → dur → buffer → distant)

**Statut** : idée notée, NON implémenté.

### Le problème
Le catalogue local grandit en continu ; le stocker tout en RAM n'est pas
viable. On veut un catalogue hiérarchique par couches, avec chargement
paresseux et invalidation par `last_modify`.

### Les couches (futures)
1. **`catalogue_local`** (en cours) — le catalogue chaud, lisible à chaud.
2. **`catalogue_dur_local`** — plus gros, plus lourd, plus complet, stocké
   durablement (disque/BDD). Si un accès `value` (pas `list`) d'une donnée
   n'est pas dans le catalogue local, on va la chercher dans le catalogue
   dur et on la charge.
3. **`catalogue_buffer`** — tampon pour les échanges avec les catalogues
   distants (réception/émission de données).
4. **`catalogue_distant`** — catalogues externes (communautaires, entreprise,
   github/dépôt).

### Invalidation par `last_modify`
`last_modify` sert aux flux d'infos : si `modify_date` de la data est
INFÉRIEURE à `last_modify`, la data est périmée → recharge depuis la couche
supérieure. C'est le mécanisme de base du buffering futur.

### Règles
- Les lectures `value` (pas `list`) déclenchent le chargement depuis la
  couche supérieure si absente.
- Les lectures ne font JAMAIS d'écriture (mode=ro) ; `last_access` est
  bufferisé en mémoire et flushé par le writer.
- `last_modify` est mis à jour par le writer seul.
- Partage (`share_get_data` à venir) : `can_be_shared` par tag
  (non/everyone/enterprise/friends/official), `source_and_sharing` par
  data, `shared_default` pour les valeurs par défaut.

---

## Idée 4 — Wrapper CLI avec sudo activable/désactivable

**Statut** : idée notée, NON implémenté.

Un wrapper CLI autour des commandes ModelWeaver (installer, déployer,
gestion de services système, écritures hors home) avec une option
`--sudo` activable/désactivable :

- Par défaut **désactivé** : toute commande qui aurait besoin de privilèges
  échoue proprement (message explicite) plutôt que de tenter sudo
  silencieusement.
- Quand activé : les commandes sensibles passent par sudo, avec log de
  l'escalade (qui, quand, quelle commande) — traçabilité des actions
  privilégiées.
- Connexion avec `catalogue_path` (Idée 3) : les paths système
  (`/install_mw` → `/bin/modelweaver`, `usr/` → `/usr`) ne sont
  accessibles en écriture qu'avec le wrapper sudo actif — cohérent avec
  l'interdiction `*` en écriture et la sémantique ref/path.
- Usage ciblé : installations de paquets, droits de lecture sur fichiers
  système, déploiement de services — pas le fonctionnement nominal.

---

## Idée 5 — `.spec` auto dans tout service/module/autre (headers déduits)

**Statut** : idée notée, NON implémenté.

Chaque service/module/autre porte un dossier/spec `.spec` auto-généré,
contenant au minimum :

- `.use` — les dépendances/imports effectivement utilisés.
- `.spec_idea` — ce que le module est CENSÉ faire (honnêtement, en clair) :
  le but, pas l'implémentation.
- `/.headers` — les ENTÊTES de définition, façon C++, déduites du code.

### Principe clé : les headers sont DÉDUITS, pas l'inverse
On ne part PAS des headers pour écrire le code. On part du code réel et on
DÉDUIT les headers (génération automatique). Ex. pour un fichier `A.py` :

```
A.header.txt =
    def interface_gui:
        reload()
        exec_button(name_button)
        ...
```

(bref : juste les en-têtes de définition — signatures, noms, types d'entrée/
sortie, sans le corps.)

### Pourquoi
- **Contexte léger** : passer `A.header.txt` (quelques lignes) plutôt que le
  code complet d'un module à un LLM / un agent, pour un contexte.
- **Hardcheck facile** : vérifier qu'une implémentation respecte bien
  l'interface déclarée (signatures, types) sans re-parser tout le corps.
- **Couplage à la résolution typée** (le langage YAML `team.chatroom.read()`) :
  les headers déclarent les méthodes + leurs types d'entrée/sortie
  (list/singleton/singleton_or_none) → le PathEvaluator peut hardcheck une
  chaîne contre les headers au lieu de la BDD.

### Notes
- Génération : un outil/agent qui scanne les modules et écrit les
  `*.header.txt` (déduction de signatures + types).
- Vérification : un hardcheck compare l'implémentation actuelle au header ;
  écart → alerte.
- Peut alimenter `catalogue_local` (les headers comme ref/value) et la
  résolution runtime.

---

## Idée 6 — Variables `$$` persistantes + sauvegarde/optimisation

**Statut** : idée notée, NON implémenté (ajouté pendant la résolution `$`).

Le langage de résolution `$var` (hiérarchique : agent → skill → subskill) est
prévu. En PLUS, à terme :

- **Variables `$$`** : données persistantes, stockées soit dans les données
  actuelles du FSM, soit dans un état durable.
- **Méthode de sauvegarde des données** : persistance explicite des variables
  `$$` (sur disque/BDD) au lieu de tout garder en RAM.
- **Optimisation** : ne sauvegarder que ce qui change, chargement paresseux.

Connexion : les scopes hiérarchiques (`agent.var_x =`, `$var_x` résolu en
remontant) + le `$$` pour les données qui survivent aux runs.

### Grosses données optimisées (fichiers, etc.)
Les `$$` servent aussi à manipuler des GROSSES données de façon optimisée :
- `$$file_x = read("path")` lie `file_x` à `path` et fait un read.
- Si la donnée n'est PAS UTILISÉE pendant un délai (ex. 10 min), OU si on a
  besoin de place (future « taille max RAM par agent ») → **fermeture
  automatique** du handle (fichier fermé, données déchargées de la RAM).
- Au prochain accès à `$$file_x` → **réouverture paresseuse** (re-read, la
  donnée revient en RAM à la demande).
- Permet de décharger/récharger sans que l'agent ait à s'en soucier
  (transparent pour le code du skill).

