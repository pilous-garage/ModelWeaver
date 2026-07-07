# Carnet de bord — ModelWeaver

> Vision, architecture, marque et stratégie de l'écosystème.

---

## 1. Vision & Architecture

### 1.1 ModelWeaver — le socle

ModelWeaver est l'orchestrateur IA cross-platform. Il installe et coordonne
Ollama, LiteLLM, OpenCode et Open WebUI. C'est la porte d'entrée *gratuite* de
l'écosystème.

### 1.2 L'Univers (nom à trouver)

Le cadre global pour les jeux, outils et créations personnelles.
**Wizardia** était la piste, mais le nom est un peu pris — recherche en cours
pour un dérivé proche. Ce univers est distinct du noyau technique ModelWeaver
(Open Source).

### 1.3 Architecture modulaire

Séparation stricte entre le noyau technique (ModelWeaver, Open Source) et les
projets créatifs personnels (l'Univers).

### 1.4 Split technique — V0.2 (Terminée ✅)

Refactor de `modelweaver.py` en 9 modules interconnectés (3 couches).

Chaque module a un **nom mythologique** interne (identité de marque), mais son
interface technique reste référencée par son rôle (orchestrator, install, common)
pour rester modulaire et interchangeable.

Seul **common** est obligatoire — il assure l'audit machine et fournit les
interfaces. **install** et **orchestrator** sont remplaçables : n'importe qui
peut proposer sa propre implémentation, du moment qu'elle respecte les
interfaces définies dans common.

Exemples :
- **Merlin** = notre orchestrateur (référencé techniquement comme `orchestrator`)
- **Chopin** = potentiel module de composition/agents
- **Hephaistos** = notre installeur (référencé techniquement comme `install`)

On ne renomme PAS les modules des autres — on fait du modulaire, pas de
l'enfermement propriétaire.

---

## 2. Architecture Actuelle (V1)

(Conservé depuis `ModelWeaver.md` — voir ce fichier pour les détails techniques.)

**En bref :**
- Deux phases : `modelweaver.sh` (bootstrap sh) → `modelweaver.py` (cœur Python)
- Modes YES / NO / ASK
- Composants : ollama, litellm, opencode, open-webui
- OpenCode first : point d'accès principal pour toutes les LLM

---

## 3. Stratégie de Croissance

### 3.1 Développement piloté par l'usage

Utiliser l'IA pour générer les scripts de démo et automatiser le marketing.

### 3.2 Marketing "Preuve par l'exemple"

Vendre l'efficience et la résolution de problèmes réels, pas des
fonctionnalités marketing. Les démos doivent montrer ModelWeaver résolvant
des vrais cas d'usage.

### 3.3 Mascotte IA

Créer une identité visuelle et sonore pour la marque, maintenant une
interaction constante avec la communauté.

---

## 4. Gestion Communautaire & Gouvernance

### 4.1 Modèle "Dictateur Bienveillant"

Déléguer la maintenance technique aux modérateurs/contributeurs, garder le
veto sur la vision stratégique.

### 4.2 Transparence radicale

Les logs et la télémétrie servent de base unique pour critiquer ou
recommander des outils. Ça évite les conflits juridiques et favorise
l'objectivité.

---

## 5. Stratégie de Monétisation

### 5.1 "Appât de luxe"

**ModelWeaver est gratuit** — c'est la porte d'entrée qui bâtit une
confiance solide.

### 5.2 Produits payants séparés

Monétiser uniquement :
- Les jeux (Univers)
- Les extensions propriétaires de l'Univers qui apportent une valeur ajoutée unique

---

## 6. Identité de Marque

### 6.1 Mythos

Utiliser des références magiques/mythologiques pour structurer l'expérience
utilisateur.

- Les "Arcanistes" manipulent le code/tissage
- Les modules internes portent des noms mythologiques (Merlin, Chopin…)
- La documentation technique reste **claire et technique** — le mythos est
  une couche d'identité, pas un obscurcissement

### 6.2 Nommage

Recherche en cours pour :
- Le nom de l'Univers (dérivé de Wizardia, libre de droits)
- Marque : **ModelWeaver** conservé pour le projet technique

---

## 7. Philosophie & Charte

Repris de `ModelWeaver.md` — règles d'or inchangées :
- Citoyenneté numérique, respect des fournisseurs
- Architecture découplée, chaque brique interchangeable
- Maximisation de l'offre (basique/local vs complexe/API)
- OpenCode first
- **Les limites d'une version sont définies AVANT de coder. Pas d'extension de périmètre en cours de route.**
- **Parmi les limites obligatoires** : toute version peut imposer des refactors (ex: V0.2 force le split common/install/orchestrator)

---

## 8. Mise à jour de session — 2026-07-05

### Bridge OpenCode isolé
- La commande `opencode` reste directe et n’est plus remplacée par le wrapper ModelWeaver.
- Le bridge ModelWeaver est installé sous `opencode-modelweaver` pour les usages explicites.
- Le wrapper envoie les requêtes vers LiteLLM, enregistre la trace de route et tente les modèles de secours de façon visible.
- Les erreurs d’authentification et de saturation sont désormais traitées comme des déclencheurs de fallback, avec logs dans `.modelweaver/route_trace.log`.

### État opérationnel
- LiteLLM est vérifié et reachable sur `http://127.0.0.1:8000/health/readiness`.
- Les tests de régression couvrent la génération du wrapper, le plan de routage et le comportement de fallback.

### Décisions architecturales (2026-07-06)

**SmarterRouter abandonné** : Archivé sous `SmarterRouter_ARCHIVED/`. Trop complexe, ne gérait pas correctement le multi-providers natif. Le `RouterEngine` ne découvrait que les modèles du provider principal, les providers externes n'étaient pas routables automatiquement.

**Remplacé par** : Proxy custom `litellm_router_proxy.py` basé sur `litellm.acompletion()` avec :
- Essai séquentiel des modèles en ordre de priorité
- Budgets contexte par modèle (Groq=100K, Gemini=1M...)
- Fallback automatique si budget dépassé ou erreur
- Signature `[Répondu par : X]` dans la réponse
- Streaming supporté

**Contexte projet** : gitingest intégré au proxy. Injection auto de l'arborescence + fichiers clés à chaque requête. Plus de "donne moi tel fichier" en boucle.

**Pipeline actuel** : `opencode → Proxy context-aware (:8000) → [15 modèles, 9 providers]`

### Automatisation dans modelweaver.py

| Étape | Statut |
|-------|--------|
| Installation composants | ✅ `modelweaver.py` |
| Génération config LiteLLM | ✅ `modelweaver.py:1271` |
| Découverte modèles | ✅ `test_model_connectivity.py` |
| Gestion fallback/backoff | ✅ `modelweaver_proxy.py` |
| Proxy context-aware + gitingest | ✅ `.modelweaver/litellm_router_proxy.py` (service systemd) |
| Auto-génération opencode.json | ✅ (déjà configuré global + projet) |
| Budgets contexte | ✅ `context_settings` + `model_budgets` dans YAML |

## 9. Roadmap

| Version | Périmètre | Statut |
|---------|-----------|--------|
| V0.1 | Socle : install, audit, composants, fallback, CLI, GUI | ✅ Terminée |
| V0.2 | Split en 9 modules (3 couches) | ✅ Terminée |
| V1.0 | Version publique distribuable (cible de stabilité) | 🎯 Objectif |
| Vn+ | Univers, jeux, mascotte, monétisation | 📝 Idées |

### Idées V0.2+
- **gitingest intelligent** : adapter la taille du contexte injecté par modèle (Gemini peut recevoir ~2M chars, Mistral/OpenAI ~400K). Implémenté dans `litellm_router_proxy.py` via `try_deployments` avec troncature par budget. À améliorer : fenêtres préférées configurables par modèle/groupe, priorisation des fichiers les plus pertinents plutôt que simple troncature FIFO.

Détail des versions dans `VERSIONS.md`.
