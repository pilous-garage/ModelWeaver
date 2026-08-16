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
