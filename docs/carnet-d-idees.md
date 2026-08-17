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

---

## Idée 7 — Benchmarks de code pour swarm (évaluation automatique)

**Statut** : idée notée, NON implémenté. Source : discussion Gemini.

Le swarm est taillé pour le COMPLEXE, pas pour `addition(a,b)`. Deux familles
d'évaluation automatique :

### Évaluation fonctionnelle (boîte noire) — la seule fiable
- L'évaluateur exécute le code généré avec des jeux d'entrées cachés et
  vérifie des égalités strictes. Verdict binaire Pass/Fail.
- Zéro coût, millisecondes, reproductible. C'est le « juge de paix ».
- **Tiny-HumanEval** : 10-15 problèmes ciblés (5 faciles/5 moyens/5 complexes).
- **SWE-bench Lite maison** : 5-10 micro-projets cassés → le swarm doit
  modifier le code pour faire passer les tests unitaires (succès binaire).
- **Mini-GAIA** : questions multi-étapes avec réponse unique (format strict) —
  parfait pour démontrer la valeur du swarm (délégation d'étapes).

### LLM-as-a-Judge — à éviter pour du benchmark rapide
- Coût/lenteur : chaque éval = un appel LLM puissant → double le temps.
- Variance : même à temp 0, un LLM peut changer d'avis (voir Idée 11).
  Biais : sur-note le code qui ressemble à son style.
- Réservé à des critères qualitatifs (lisibilité, sécurité) en complément.

### Ordres de grandeur (HumanEval/MBPP)
- Modèles standards : ~80-88%. Reasoning natifs : ~92-95%. Open-source
  légers : ~45-65%. Un swarm auto-correctif peut transformer 60% → 90%+.
- Sur SWE-bench, les meilleurs swarms (Devin/Factory) ne font que 20-40%.
- Le « plafond » : rigidité des assertions, timeouts, hallucinations longues.

### Benchmarks lourds (complexité réelle)
- **SWE-bench (Verified)** : vraies issues GitHub, patch → tests officiels du
  dépôt. Un run = 5-30 min par problème. Le boss final des swarms.
- **GAIA** : assistants multi-étapes multi-modales, réponse unique.
- **AIME** : maths compétition, réponse = entier 0-999, évaluation instantanée
  mais réflexion longue (o1/o3).

---

## Idée 8 — Routage cognitif par niveau (matrice role/level/llm)

**Statut** : idée notée, NON implémenté. Source : discussion Gemini.

Ne pas demander un modèle frontière pour du formatage. Cascade de coûts :

1. **Filtrage de contexte** : modèle économique/rapide ingère les logs/greps et
   extrait le snippet pertinent → transforme 100k tokens en ~3k avant que le
   manager ne lise.
2. **Découpage par niveau** : le modèle semi-frontière découpe en tâches avec
   difficulté (débutant, junior, intermédiaire, senior).
3. **Matrice de routage** : chaque (rôle, niveau) → LLM adapté. Junior =
   modèle flash/distillé (~0.15$/M), intermédiaire = standard (~2$/M), senior
   = frontière (~15$/M). La boucle codeur↔reviewer tourne sur du pas cher.
4. Le frontière n'intervient qu'au merge/validation finale.

### Apprentissage empirique (traces)
- Stocker la trace complète des transitions : [découpeur:LLM_A] → [codeur:LLM_B]
  → [reviewer:LLM_C] → [échec test] → [retour codeur] …
- **Taux de frottement** d'un couple d'agents : 1.2-1.5 cycle = bon ; 4-5 =
  prompts/reviewer mauvais.
- **Goulots** : une transition `error` systématique après un nœud LLM_X =
  facteur limitant.
- **Matrice de confiance locale** : fichier `llm_performance_matrix.json`,
  incrémenté à chaque convergence réussie, décrémenté sinon.

---

## Idée 9 — Communauté d'apprentissage (scores role/level/llm + réputation)

**Statut** : idée notée, NON implémenté. Source : discussion Gemini.

### Partage à granularité variable (3 niveaux)
1. **Signal statistique pur (IP-safe)** : triplet isolé (rôle, niveau, llm_id)
   + métriques de succès. Zéro contexte. Parfait pour entreprises.
2. **Topologie d'équipe** : [(rôle, niveau, llm) × n] pour un type de tâche —
   la « recette » macro, sans les prompts.
3. **Précision absolue** : l'agent clé-en-main (prompt système + grille) —
   open-science pour hobbyistes/étudiants.

### Réputation anonyme (User × Data_type × LLM)
- **Identité par clé publique** (pas de compte/email) : clé privée signe les
  envois, clé publique = l'ID anonyme (`0x7a2b…`). Anonymat garanti, plusieurs
  ID possibles.
- **Matrice de confiance** : score par `data_type` (security_audit,
  llm_benchmark, swarm_team_recipe…) — un utilisateur peut être 95% en sécu
  mais 40% en benchmark. La pénalité ne touche que la colonne fautive.
- **Pondération** : poids final = score[user, data_type] × modificateur[llm]
  (frontière ×1.2, petit local ×0.5).
- **Auto-ajustement** : écart au consensus (la « vérité du terrain ») →
  récompense/pénalité par data_type.
- **Filtre de sécurité** : n'importer des agents/recettes que si l'auteur a un
  trust > seuil. Certification 🟢 par consensus pondéré (≥ X audits frontière).

### Partage d'objets
- Partager agent / skill / recette d'installation / team_complète, choix
  individuel par objet (un super-agent privé, un résumeur de mail partagé).
- **Autocodage communautaire** : les utilisateurs allouent ~5% de leur budget
  LLM inutilisé pour améliorer le projet (voir Idée 10).

---

## Idée 10 — Autocodage collaboratif (branches par ID + GitHub gratuit)

**Statut** : idée notée, NON implémenté. Source : discussion Gemini.

### Branches par identifiant
- Convention : `autocode/user-<ID_anonyme>/task-<id_tache>`. Chaque instance
  locale travaille en isolation stricte sur sa branche nommée d'après son ID.
- **Merge local uniquement** : fetch dev-auto → checkout sa branche → swarm
  code/test → rebase local sur dev-auto (résolution de conflits en local par
  un agent git-fixer) → push UNIQUEMENT si les tests passent.
- GitHub devient un hub de réception de PR, pas une zone de merge robot.

### GitHub comme backend gratuit (pas de serveur relais)
- **GitHub App token restreint** : droits uniquement sur les branches
  `autocode/*` + PR. Si volé, pire cas = branche spam.
- **GitHub Actions comme « serveur relais virtuel »** : au push, un workflow
  vérifie la signature du commit (clé privée de l'utilisateur), interroge le
  trust score, merge dans dev-auto si valide, supprime la branche sinon.
  Scalable à 0€ (infra GitHub), même avec des milliers de connexions.
- **Git-as-backend pour la confiance** : une branche `database` orpheline
  contient `trust_scores.json` — les Actions lisent/écrivent via git (verrous
  et stockage gérés par GitHub).
- **Signature des commits** : `git commit -S` avec la clé privée anonyme →
  le badge « Verified » par clé, pas par compte GitHub. Zéro compte à
  configurer pour les utilisateurs.

### Contrôle humain
- `main` = branches protégées GitHub : PR obligatoire + approbation humaine.
- `dev-auto` = le swarm a les pleins pouvoirs (merge auto si CI verte).
- Une fois par semaine, un humain merge dev-auto → main.

---

## Idée 11 — Non-déterminisme des LLM et benchmarks

**Statut** : idée notée, NON implémenté. Source : discussion Gemini.

Un LLM n'est pas déterministe même à même prompt : température/sampling
(choix probabiliste) ET parallélisme GPU (erreurs d'arrondi, ordre des
calculs). Impact direct sur les benchmarks : reproductibilité.

- Pour des évaluations comparables, imposer `temperature: 0` + `seed: 42` +
  `top_p: 1` sur tous les nœuds de test/audit.
- Si deux utilisateurs testent la même recette avec le même modèle, même
  configuration → même score à soumettre à la matrice de confiance.
- Un audit à température 0.7 peut rater une faille qu'un modèle frontière
  (temp 0) voit — d'où la pondération par modificateur LLM (Idée 9).

## Idée 12 — ModelWeaver en Docker : tout passe par le bridge hôte

**Statut** : idée notée, NON implémenté. Source : session taskflow (2026-08-13).

Un ModelWeaver lancé en Docker (ex. tests d'un selfimprove, environnements
isolés) ne doit **jamais appeler les LLM directement** : tous les `llm_call`
doivent passer par le **bridge de l'hôte**.

### Pourquoi
- **Perte de log calls** : les appels LLM du conteneur ne seraient pas comptés
  dans `model_call_log` de l'hôte → métriques d'usage incomplètes.
- **Dépenses de budget non comptées** : le budget (money/tokens) serait
  consommé hors du compteur → dépassement invisible, pas de disjoncteur.
- **Cohérence d'allocation** : l'allocation LLM (capacités, backoff, quota)
  vit sur l'hôte ; un conteneur isolé aurait une vision tronquée et pourrait
  choisir un modèle en backoff ou hors budget.

### Principe
- Le conteneur se déclare `mode=remote-bridge` : `direct_bridge.chat()` (et
  `resilient_chat`, `test_agentic`, etc.) envoie la requête à un endpoint RPC
  de l'hôte au lieu d'appeler le provider.
- L'hôte exécute l'appel réel, enregistre `model_call_log` / `capacite_log` /
  budget, et retourne la réponse (voire les chunks streaming).
- Aucune clé API dans le conteneur : le KeyManager reste sur l'hôte.

### Prochaines étapes
- Endpoint RPC du bridge hôte (`bridge/chat`, `bridge/chat_stream`,
  `bridge/test_agentic`, `bridge/get_capabilities`).
- Flag d'environnement `MW_BRIDGE_REMOTE=http://host:port/...` lu au boot.
- Validation : deux modelweaver (hôte + docker) → les appels du conteneur
  apparaissent dans `model_call_log` de l'hôte avec `caller_id` du conteneur.

## Idée 13 — Timeout 300s sur les appels LLM + scores par méthode

**Statut** : idée notée, NON implémenté. Source : session scoring batch (2026-08-14).

### Timeout 300 sur les call
- La limite d'appel est 300s (5 min). MAIS le timeout ne doit PAS interrompre un
  **streaming en progrès** : si des chunks arrivent régulièrement, l'appel est
  considéré SUCCÈS (pas timeout). Le timeout s'applique seulement si aucun
  progrès pendant la fenêtre.

### Scores par méthode d'appel
- La méthode de score actuelle (bucket succès/fail par période) est adaptée au
  chat/response (un appel = une réponse finale). Pas adaptée au **streaming**
  (succès = début du flux + progrès) ni à la **completion** (succès partiel /
  tokens produits).
- Prévoir d'autres méthodes de score par `call_type` (chat, streaming,
  completion) quand le timeout/succès sera défini par méthode.

### Conclusion scoring (retenu pour le batch)
- Stocker des COMPTEURS nb_success / nb_fail par bucket (jamais des scores) —
  un bucket 5 min sans appel ne doit pas devenir "total succès".
- `score_zone = (1 + nb_success_zone) / (1 + nb_total_zone)`, calculé par somme
  des compteurs de la zone, indépendamment par zone (pas de pondération des
  scores : trop de lissage, trop de bénéfice pour les LLM jamais appelés).
- Rollup : la somme des buckets d'un niveau donne le premier bucket du niveau
  supérieur.

## Idée 14 — Évaluer aussi les AGENTS (pas seulement les LLM)

**Statut** : idée notée, NON implémenté. Source : session benchmark by experience (2026-08-14).

Le benchmark par expérience ne note que les **LLM** (les seuls porteurs de qualité) :
le supervisor (sans LLM) et as_llm_leader (sans LLM, recopie le prompt en tâche)
ne sont PAS notés sur leur création de tâches.

Mais il faudra AUSSI évaluer les **agents** (la qualité de l'orchestration, les
décisions) : capacité du découpeur à découper juste, pertinence des choix de
modèles, réglages des workflows. C'est un second niveau d'évaluation, à part.

- Séparer l'évaluation LLM (par modèle) de l'évaluation agent (par agent) dans
  le benchmark par expérience.
- L'évaluation agent pourrait croiser : difficulté assignée vs difficulté perçue,
  pertinence de la découpe, choix du modèle selon la tâche.

## Idée 15 — Swarm-as-llm : repo par requête + team_leader de management (LLM simple)

**Statut** : idée notée, NON implémenté. Source : session benchmark HumanEval (2026-08-15).

### Problème
Le swarm-as-llm (`run_completion`) ne crée **aucun repo** pour une requête
(`task.repo` vide). Conséquences :
- les codeurs n'ont pas de repo où écrire → aucun fichier produit → le respond
  et les explorers bouclent sur des « livrables inexistants » (`git log` échoue,
  `ls` vide), chaque sample benchmark prend 10-60 min.
- la réponse finale est un résumé texte, pas le code produit.

### Solution : repo par requête (géré par l'agent endpoint)
- `run_completion` (l'agent qui sert l'endpoint, as_llm_leader / un service)
  crée un repo de session `sessions/<requete_id>` via `create_session` (déjà
  existant !) avec la prompt seed + fichiers fournis, puis rattache le repo à
  la tâche (`task.repo = sessions/<requete_id>`).
- Les codeurs `git_clone sessions/<requete_id>`, écrivent, commitent.
- `_taskflow_reply` construit la réponse = **prompt originelle + `git diff`
  entre le commit seed (début) et le commit final (où la réponse se prépare)**.
  On peut même se servir du contexte pré-généré (le diff) pour répondre
  directement, sans llm_call coûteux.

### Évolution : team_leader de management sur un LLM simple
- Actuellement `as_llm_leader` est team_leader SANS LLM (mécanique pure).
- Idée : un AUTRE team_leader dédié au MANAGEMENT (ask_auth, arbitrage,
  décision simple) sur un LLM très simple/rapide, permettant de BIFURQUER
  quand la question est assez simple pour ne pas justifier le swarm complet
  (consensus direct). Ex. une requête « dis bonjour » ne devrait pas déclencher
  analysis → coding → review → respond ; un leader simple répondrait direct.

## Idée 16 — Règles de préférence de modèles pour le consensus (obtenu/pas_obtenu)

**Statut** : idée notée, NON implémenté. Source : session agent consensus (2026-08-16).

Quand l'agent consensus demande ses 5 modèles différents, on pourrait à terme
utiliser des RÈGLES de préférence (espèce de heuristique) :
`modèle → obtenu/pas_obtenu → nouveau_modèle` — un mapping qui guide le choix
du modèle suivant selon si le précédent a été obtenu ou non. Pour l'instant :
on demande juste au bridge 5 modèles différents (exclusion cumulée).

Le fallback aussi : si un modèle échoue, on met à jour la liste et on redemande
un modèle DIFFÉRENT des autres déjà pris.

## Idée 17 — Architecture consensus + supervisor général + tâches de sub-agent

**Statut** : architecture définie (session 2026-08-16), implémentation par étapes.

### Système A — Agent consensus
- Table `question` (id_question, question, id_creator, status_answering) + `reponse`
  (id_question, id_agent, contenu, commit vide = texte clair).
- 5 answering_machine = sub-agents, chacun reçoit sa LLM (5 modèles DIFFÉRENTS).
- `ask_llm` avec exclusion : `not_same_modele(list_llm_ref)` → demande au bridge
  5 modèles distincts ; fallback → redemander un modèle différent des autres.
- `ask_llm_with_prompt` : alloue (args classiques de ask_llm) PUIS envoie la
  prompt au bridge → réponse. Échec LLM → demande UN AUTRE modèle (exclusion
  du défaillant) et retente (max_essais). C'est le pont alloc→génération.
- Flux : 5 answering_machine (threads, ask_llm_with_prompt) → attendre 3
  réponses + grace (1.5× le plus long des 3) → cancel → jugement par les 5
  (vote A/B/C ou NEW) → majorité absolue (2/3 ou 3/5)
  → élimination des non-choisies → escalade :
    * tour new autorisé (max 5 tours avec NEW)
    * puis interdire NEW
    * puis interdire de voter pour soi
    * puis grader les autres (0-1, sans égalité) → meilleure note
    * toujours égalité → au hasard.
- Un modèle peut n'avoir pas répondu (cancel) mais voter quand même.
- Escalade humaine : `ask_human` → human_choice (issue bloquée) au lieu du hasard.
- Similarité A≈B : non tranché (colonne similar_to envisagée).

### Système B — Tâches de sub-agent
- `task_for_subagent(id_subagent)` : crée une tâche attributed/id_agent (pas
  unattributed). Simplifie les call explore.
- `create_task` flag waiting/no_waiting ; `create_task_list` (liste, flag global :
  attendre que toutes soient done/supervised/cancelled).
- Table `blocking_agent` (id_task, id_agent) : réveille l'agent quand ses tâches
  blocking sont terminées (le supervisor fait waker).

### Système C — Supervisor général + teams
- Un supervisor GÉNÉRAL vérifie que chaque team a son supervisor, les réveille.
- team_default : tous les agents sans team, avec son propre supervisor.
- Chaque team a son supervisor ; on déclare les RÈGLES, pas le supervisor lui-même.
- Steps du supervisor :
  * check_awake : attributed/doing → is_awake? sinon is_blocked? sinon waking.
  * check_done/cancelled : statuts cancelled_done / cancelled_supervised à ajouter.
  * step_cancel : tâche cancelled (mais pas cancelled_done/supervised) → envoyer
    signal cancel à l'agent ; l'agent reçoit cancel_done → cancel_supervised.
  * cancel = entrypoint de plus haute priorité (arrêt des calls LLM).
- Entrypoints par défaut : un entrypoint peut être NON déclaré (version défaut)
  ou déclaré. Lors de l'inlining d'un agent, prendre les défauts.
  Tout signal = un entrypoint (cancel, pause, ask_auth, receive_auth...).

### Système D — Flux pick/attribution (déjà committé 6c532ad)
unattributed → attributed → doing → done → supervised + too_hard → bump/découpe.

## Idée 18 — Assignation LLM correcte et évolutive (niveaux + expérience)

**Statut** : PLANIFICATION DÉTAILLÉE — ne pas coder avant d'avoir réglé budgets/coûts.

### A. Niveaux de compétence
- Niveaux : debutant / junior / intermediaire / senior / expert.
- Chaque niveau = un seuil de score + un rôle approprié (debutant suffit pour
  la classification simple ; senior requis pour coding complexe).

### B. Tables de score (par niveau, PAS de cube)
- 2 tables plates, une ligne par couple, 5 colonnes niveau :
    llm_domaine_score:   (llm_id, domaine_id, debutant, junior, intermediaire, senior, expert)
    llm_task_type_score: (llm_id, task_type_id, debutant, junior, ...)
- Domaine (nature) : text, code, math, vision, reasoning, data...
- Type de tâche (pipeline) : planning, coding, reviewing, testing, merging,
  respond, exploration...
- Pourquoi par niveau : un LLM peut être excellent en debutant/junior mais
  dégradé en senior (dégressif), un autre croissant linéairement. Le niveau
  permet de recruter le LLM le plus CHEAP qui atteint la tranche demandée.

### B2. GRANULARITÉ DU SCORE : provider/model/ENDPOINT
- Le score n'est pas par LLM générique, mais par (provider, model, endpoint) :
  le MÊME modèle servi par 2 endpoints différents (ex. opencode-zen vs nvidia)
  n'a ni les mêmes perfs ni les mêmes coûts ni la même fiabilité.
- L'endpoint en général compte AUSSI (latence, quota, disponibilité, clé).
- Les tables de score sont donc indexées par (provider_id, model_id,
  endpoint_id, domaine/type, niveau) — l'adresse complète
  (provider_model_address.adresse_id, cf. llm_allocation/address.py) est la
  clé commune de référence.
- Les événements d'expérience (task_log + signaux externes) référencent aussi
  cette adresse, PAS un nom de modèle générique.

### C. Init = 1.0 partout
- On part de score=1.0 sur toutes les cases (neutre, PAS pénalisant).
- Le scraping de benchmarks sert au plus de léger biais de départ, pas de
  vérité. Aucun modèle n'est écarté injustement au départ.

### D. Mise à jour : fraîcheur + stabilité, scoreur BATCH (pas instantané)
- Les événements sont LOGGUÉS (pas de mutation du score à la volée).
- Un SCOREUR BATCH recalcule périodiquement (toutes les 10 min pour l'instant ;
  on loggue la durée du recalcul).
- Fraîcheur : poids d'un événement = exp(-âge / demi_vie).
- Demi-vie ADAPTATIVE : demi_vie = min(âge_premier_scoring,
  âge_plus_vieux_scoring / 10). Un modèle récent converge vite, un éprouvé
  est stable (5 échecs sur 10000 réussites ne le font pas chuter).
- Stabilité : amortissement par volume (poids_effet ∝ 1/(1 + N_total*k)).
- Score = (Σ poids × résultat) / (Σ poids) — moyenne pondérée temporelle (EWMA).

### E. Table root_tasks (unicité durable)
- Table séparée : root_tasks (root_id PK AUTOINCREMENT, workspace_id,
  prompt_hash, created_at). root_id IMMUABLE (jamais réutilisé).
- task_log référence root_id (PAS le task_id de la table tasks, qui peut être
  vidée/purgée). Le log reste durable via root_tasks.

### F. Log des tâches (base, sans grosses données)
    task_log:
      log_id        INTEGER PK
      task_id       INTEGER
      root_id       INTEGER      → root_tasks.root_id (racine utilisateur)
      parent_task_id INTEGER NULL → qui a produit la tâche (cheminement)
      ordre         INTEGER      → position pipeline
      llm_id        INTEGER      → LLM qui a traité
      role          TEXT         → planning/coder/reviewer/tester/merger/respond
      domaine       TEXT
      task_type     TEXT
      exit_signal   TEXT         → done/too_hard/error_repéré/cancelled/bumped
      created_at    TEXT
- Colonne exit_signal AUSSI dans la table tasks (reflet).
- On enregistre les tâches too_hard → modify_difficulty.

### G. Cheminement (parent = qui PRODUIT, pas les dépendances de données)
- Une DÉCOUPE ne crée que du MÊME type (coding OU testing OU review), jamais
  un mix. Les n coding + le merge autogénéré ont la découpe (planning) comme
  parent. PAS les étapes intermédiaires.
- Chaîne type :
    planning (découpe) → coding×n + merge_autogénéré → review (règle) →
    testing (règle) → merge (règle) → respond (finalisation)
- review.parent = coding ; testing.parent = review ; respond.parent = merge.
- root_id = racine pour TOUS.
- Trace d'erreur : testing échoue → parent=review → parent=coding →
  parent=planning. On pénalise les maillons fautifs.

### H. Renommage : analysis → planning
- La sub_task du découpeur devient  (il planifie/split, n'analyse
  pas). Impact : TASK_TAGS, ROLE_TO_SUBTASK, règles supervisor, greedies.

### I. Récompense GLOBALE (par root_task, pas tâche isolée)
- À la fin d'une root_task, le scoreur passe TOUTE la chaîne :
  * Réussite → bonus à TOUS les (llm, rôle, domaine/type) qui ont contribué.
  * Échec localisé → on remonte le parent → pénalité aux maillons fautifs
    (testing échoue → tester + reviewer qui a raté + coder + découpeur si
    mauvaise découpe).
- Chaque événement = (root_id, llm_id, rôle, pénalité/bonus).

### J. Signaux externes (renforcement)
- En plus des événements supervisor, on injecte les benchmarks externes :
  (llm, domaine, niveau, pass/fail) avec le même mécanisme fraîcheur/stabilité.
- Table d'événements commune, source : supervisor | benchmark.

### K. Allocation (reste à affiner avec budgets)
- allocation(rôle, type, domaine, niveau) :
  * candidats = llm dont score[domaine][niveau] ≥ seuil ET score[type][niveau] ≥ seuil
  * choix = min_budget(candidats) — le plus cheap (temps/argent/thinking)
- Option : score × task_cost au lieu de seuil+cheapest (à trancher avec budgets).

### L. Budgets / coûts (PROCHAINE ÉTAPE — pas encore défini)
- Dimensions : temps, argent, tokens, thinking_power.
- Portées : per-requête (borne le choix), per-zone (team/projet/workspace),
  per-état (service LLM manager).
- Régulation : mode dégradé (modèles gratuits) ou blocage quand budget atteint.

### M. RÉCAP CONCEPTION COMPLÈTE (session 2026-08-17)

#### M1. Scoring LLM (expérience évolutive)
- Niveaux : debutant / junior / intermediaire / senior / expert.
- 2 tables de score PAR NIVEAU (pas de cube) :
  - llm_domaine_score:   (llm_id, domaine_id, debutant, junior, intermediaire, senior, expert)
  - llm_task_type_score: (llm_id, task_type_id, debutant, junior, intermediaire, senior, expert)
- Init = 1.0 partout (neutre, pas pénalisant). Le scraping = léger biais max.
- Granularité : provider/model/ENDPOINT (adresse) + model_key (modèle canonique).
  Le model_key = grain canonique (22 variantes de deepseek-v4-flash = 1 modèle).
- Mise à jour BATCH (pas à la volée) : fraîcheur (demi-vie adaptative =
  min(âge_premier_scoring, âge_plus_vieux_scoring/10)) + stabilité
  (amortissement par volume : 5 échecs sur 10000 ≠ 5 sur 10).
- Événements : task_log (supervisor) + signaux externes (benchmark pass/fail).
- Récompense GLOBALE par root_task (tous les contributeurs, ou tous les maillons fautifs).

#### M2. Adresses
- Clé en clair : RAM uniquement (jamais sur disque dans logs/budgets).
- adresse_key_tag (DURE, sans clé, partageable) :
    adresse_key_tag_id = UNIQUE(provider_id, endpoint_id, model_provider_id, api_key_tag)
    + endpoint_url, provider_model_name, api_type, model_id, model_key, adresse_id
- adresse_runtime (DURE aussi, construite de base, garde api_key_id) :
    adresse_runtime_id = UNIQUE(provider_id, endpoint_id, model_provider_id, api_key_id)
    + adresse_key_tag_id, model_id, model_key, endpoint_url, provider_model_name,
      api_type  (dénormalisés)
- URL finale + auth = reconstruite à RUNTIME en RAM (adresse_runtime_id + clé résolue).
- Normalisation des données à la SOURCE : provider_model_name doit être NET (sans
  préfixe provider redondant, sans doublon kilo/kilo/...). Pas de patch runtime
  (_build_model_id actuel = pansement à retirer).
- sdk_access = api_type (openai/gemini/cohere) → format du corps.

#### M3. Coûts & Budgets (par niveau tag + final)
- 3 monnaies : dollar_cost, time, thinking_power.
  * thinking_power = INDICE DE PUISSANCE DE PENSÉE (capacité cognitive du
    modèle — différencie un modèle frontière d'un bas niveau). PAS les tokens
    de raisonnement. Source : score_reasoning + score_etire (benchmark étiré
    par domaine). Une clé free dépense du thinking_power pas de l'argent.
- type_budget : table de tags (type_id, nom) → token_in, token_out, request, time,
  thinking_power, money (extensible).
- Une REQUÊTE → PLUSIEURS coûts simultanés (cost_tok_in, cost_tok_out, cost_time,
  cost_req, cost_thinking_power).

- budget_key_tag (partageable par tag de clé) :
    budget_key_tag_id, type_id, quota, spent, souplesse, interval_reset,
    next_reset, session_start_condition, error_rate_limite
- cost_key_tag (partageable par tag) :
    cost_key_tag_id, adress_key_tag_id, budget_key_tag_id, unit_in, unit_out, ratio

- budget_final (par clé/adresse, dérivé du tag) :
    budget_id, type_id, quota, spent, souplesse, interval_reset, next_reset,
    session_start_condition, error_rate_limite
- cost_final :
    cost_id, adresse_runtime_id, budget_id, unit_in, unit_out, ratio

- SOUPLESSE (politique de dépassement) : strict | souple | informatif.
  * strict : vise 90% du quota (évite erreurs de calcul)
  * souple : taux d'essai (souplesse_taux, ex. 5%) pour sonder si la limite a bougé
    + backoff adaptatif (après échec de sondage, attendre X min)
  * informatif : ne bloque jamais (log seulement)
- error_rate_limite : budget en erreur (rate-limit atteint). Pour un provider avec
  min/day/month, le plus petit intervalle (min) est le budget "erroré".
- Budget raisonnable le plus petit = celui qui reset vite = à blâmer.

#### M4. État d'erreur (2 tables distinctes)
- adress_error_state (état PAR ADRESSE) :
    adresse_id PK, error_since, last_error_at, backoff_until, n_fail
- budget_error_state (état PAR BUDGET, RESTRICTION dérivée) :
    budget_id PK, error_since, last_error_at, backoff_until
  RÈGLE : le budget devient error UNIQUEMENT si TOUTES les adresses liées à ce
  budget sont en erreur (une seule adresse ne suffit pas).
  Lien budget↔adresses : via cost_final (cost_id, adresse_runtime_id, budget_id).

#### M5. Logs & mesures
- real_call_models (existant) : chaque appel avec adresse_id, sent_at/received_at,
  status (ok/rate_limited/quota_exhausted/error), tokens, cost. → VÉRIFIER qu'on
  log bien adresse_id.
- session_success + session_fail (dérivés de real_call_models, par adresse_id) :
  périodes où ça marche / rafales de fail (du 1er au dernier). Révèlent les quotas
  réels.
- really_used_budget (existant) : mesure des limites réelles observées.
- budget_calculé : colonne dans les logs (real_call_models) → le tick seconde lit
  le dernier budget_calculé SANS re-agréger (pas besoin du batcheur).
- TICK budget en temps réel : met à jour budget_consumption / adress_error_state /
  budget_error_state (par seconde).
- task_log (cheminement des tâches, base) :
    log_id, task_id, root_id, parent_task_id, ordre, llm_id, role, domaine,
    task_type, exit_signal, created_at
- root_tasks : root_id PK, workspace_id, prompt_hash, created_at (immuable).
- Colonne exit_signal AUSSI dans la table tasks (reflet).

#### M6. Granularités (récap)
| Système | Grain |
|---------|-------|
| adresse | adresse_runtime_id (provider+endpoint+model_provider+api_key_id) |
| log | api_key_id (+ adresse_id) |
| cost | api_key_tag (le type de clé détermine la facturation) |
| budget | api_key_tag (partageable) + budget_final par clé |
| erreur | adress_error_state + budget_error_state (restriction) |

#### M7. Périphériques à modifier (déjà discutés)
- Renommage analysis → planning (découpeur).
- Colonne domain dans tasks (déjà ajouté).
- Découpe = même type uniquement (coding OU testing OU review, pas un mix).
- Dédoublonnage provider_models (unique par (provider, provider_model_name)),
  et catalogue_models (unique par model_key canonique).

#### N. COÛTS PAR TÂCHE/NIVEAU + THINKING_POWER DYNAMIQUE (suite 2026-08-17)

Problème : choisir le modèle le plus approprié pour une action spécifique
(type de tâche + niveau) selon son COÛT et son BUDGET restant.

##### N1. SEQ vs PIPELINE (distinction ACQUISE)
- SEQUENCE = la liste des appels successifs d'une sous-tâche (une ligne de
  model_success_runs). Une séquence s'ouvre au premier succès et se ferme ON
  CHANGE DE TYPE (à l'ouverture de la séquence suivante) — PAS à l'échec.
- PIPELINE = l'ensemble des sous-tâches d'une même root_task. Il se CLÔTURE
  quand la root_task est finie → à ce moment on met à jour le scoring de tout
  et on enregistre le type/niveau de chaque sous-tâche du pipeline.
- Chaque appel LLM est rattaché à sa sous-tâche via meta_json du model_call_log
  (task_type + difficulty + sub_task_id) — le FSM passe déjà meta au bridge.

##### N2. La table model_success_runs (FAIT — commitée)
- Colonnes ajoutées : seq_type ('success'/'fail') + error_code.
- SPEC DE FERMETURE (implémentée via _seq_open_or_extend) : une run s'ouvre au
  PREMIER appel de son type ; un appel du MÊME type cumule (requests, tokens,
  latence) et avance seq_end = dernier appel DE LA SÉQUENCE ; un appel du TYPE
  OPPOSÉ ferme la run en GARDANT seq_end tel quel (= dernier appel appartenant
  à la séquence, JAMAIS l'appel qui change de type) puis ouvre une run du
  nouveau type. L'alternance trace AUSSI les rafales d'échec (rate-limit/quota).
- Batch (_batch_1m) et rebuild (_rebuild_sequences) utilisent le MÊME helper
  (une seule run ouverte par (provider, model)).
- Échec Error_code conservé sur la run fail.

##### N3. Le coût d'une action = 2 composantes ORTHOGONALES
- TOKENS (tok_in/out/thinking, req) : dépend du MODÈLE (comportement langagier)
  — table par model_id (comme les scores). Peut varier FORTEMENT selon le modèle
  (tok_out+tok_thinking notamment).
- TEMPS (delai_moyen, secondes/tok_in, /tok_out, /tok_thinking) : dépend de
  l'ADRESSE (socket/provider) — table par adresse_runtime_id.
- Coût total (modèle, adresse) = tokens_modèle × temps_adresse × prix_adresse.

##### N4. Tables llm_task_cost (PLUSIEURS tables, grain (modèle,type,niveau))
- table_complete : la table complète par (model_id, task_type_id, niveau) —
  35 combos par modèle — les coûts mesurés/dérivés, remplie au fil des séquences.
- table_modele_type : par (model_id, task_type_id) avec cost_ref (cost reference
  du type) — 7 combos — remplie par type (type≠niveau partagent le profil).
- table_stats_niveau : par (task_type_id, niveau) — les stats par niveau comme
  FACTEUR du cost_ref (niveau débutant = x% tok_in, y% tok_out, z% req du
  cost_ref) → pour BALANCER les combos (modèle,type,niveau) peu remplis quand on
  ne connaît que (modèle,type).
- table_level_windows : par (niveau, task_type) — thinking_power windows → cost
  estimable : si le modèle est DANS la fenêtre (thinking_power entre bornes), on
  peut espérer ce coût. Permet d'estimer le coût d'un modèle SANS séquences.

##### N5. Balancement des données clairsemées (shrinkage bayésien)
estimation_effective = w × data_spécifique + (1−w) × baseline_globale
avec w = n / (n + k), n = nb de séquences du (modèle, tâche), k = a priori (ex. 8).
Un modèle peu testé commence à la baseline (non pénalisé), ses données prennent
du poids au fil des usages. EXPLORATION : ε-greedy — fraction dédiée des
allocations vers les modèles éligibles à faible échantillon.

##### N6. THINKING_POWER dynamique (remplace l'init score_reasoning)
thinking_power(modèle, niveau) = Σ_domaines w×score(domaine,niveau)²
                                + Σ_types w×score(type,niveau)²
- CARRÉ : accentue l'écart (2.0² = 4× vs 1.0² = 1) — frontières vs bas.
- Pondérations w : toutes = 1 pour l'instant, réglables colonne par colonne.
- Somme GLOBALE (domaines + types mélangés) → un seul indice par (modèle, niveau).
- NIVEAU requis : garde seulement le score AU niveau requis de la tâche.
- INIT : scores = 1.0 partout → thinking_power = nb de combos (ex. 14) = "thinking
  haut" optimiste. Les séquences fail/success font dériver les scores → le
  thinking_power baisse/réajuste à l'épreuve des faits (même MD/AI que budgets).
- CONSÉQUENCE VOLONTAIRE : à l'init tout le monde a thinking_power = max → le
  coût dérivé est le MÊME que les frontières → on ne teste les modèles hauts QUE
  s'ils sont moins chers (normal pour tok_in/out/think : moins cher = plus haut).

##### N7. Allocation en 4 temps
1. SCORE de niveau : llm_task_type_score / llm_domaine_score au niveau requis → éligibilité qualité.
2. COÛT : llm_task_cost (coût de la tâche pour ce llm) → besoin en money/thinking/time.
3. BUDGET restant : budget_final.spent < quota (money, thinking_power, time) → éligibilité budget.
4. STRATÉGIE : sur multi-éligibles, choix par les TAUX (thinking_power/time,
   money/time) + ε-greedy d'exploration des modèles peu testés.

##### N8. TABLES DU CALCULATEUR + SUIVI PAR TÂCHE (FAIT — commitée)
- 5 tables catalogue (section N3/N4) : task_level_cost (travail par type×niveau),
  llm_effort_ratio (comportement du modèle par travail), task_level_stats (ratios
  par niveau du cost_ref senior), llm_task_cost (CACHE du produit, par
  modèle×type×niveau), llm_level_windows (fenêtres thinking_power → coût).
- services/llm_usage/calculator.py : recompute() = tick (seed stats + seed
  travail + seed effort + synthèse du produit). MISE À JOUR PASSIVE : on ne
  réécrit une ligne que si une valeur a changé (critique au tick — évite de
  toucher 100k lignes à chaque passe si rien n'a bougé).
- LEÇON : INSERT OR IGNORE / ON CONFLICT DO NOTHING MASQUENT les erreurs (SQL
  invalide, mauvaise connexion) → retour silencieux 0. Pour un cache
  re-synthétisé, utiliser un UPSERT OU une comparaison avant écriture, et
  NE JAMAIS avaler l'exception sans la logger.
- LEÇON : passer UNIQUEMENT des sqlite3.Connection aux helpers du calculator —
  passer l'objet CatalogueDB (qui a .conn) à une fonction qui attend une
  connexion, c'est une AttributeError silencieuse (masquée par le except).
- service/llm_usage/task_track.py : SUIVI BUDGÉTAIRE PAR TÂCHE. Table workspace
  task_budget_tracking (par sub_task) : budget THÉORIQUE (calculé à l'ouverture
  par le calculateur) + budget UTILISÉ (reconstruit à la fermeture depuis les
  model_call_log dont le meta_json porte sub_task_id). C'est la BOUCLE :
  théorique vs utilisé → on affine le travail/effort des tâches.
- Exemple validé : modèle 259, planning/junior ≤> travail(0.405) × effort(1.0)
  = 0.405 tok ; après effort tok_out=2.5 → 1.0125. Recompute passif → 0 écrits.
