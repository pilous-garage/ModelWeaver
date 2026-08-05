# Checklist de validation GUI ModelWeaver (V0.8.10)

Checklist complète et ordonnée pour vérifier la GUI. À cocher au fur et à mesure.
Ordre logique : base → menu Fenêtre → fenêtre vierge → menu Panneaux → les 28 panels → lazy-load → sync → persistance → boutons → divers.

## 1. Démarrage & intégrité du système
- [ ] **3 fenêtres s'ouvrent** : Installateur, Dashboard, Agent IDE (chacune sur son layout : default/dashboard/agentIde)
- [ ] **Aucun service écrasé** : daemon répond toujours (curl health), les 9 services tournent (superviseur Python inchangé)
- [ ] **Chaque fenêtre affiche le bon layout** : Installateur = installator-dashboard+deps, Dashboard = système-ressources/état, Agent IDE = sandbox-agent-ide
- [ ] **Thème sombre appliqué** dans chaque fenêtre (fond `#0f172a`)

## 2. Menu Fenêtre (dans chaque fenêtre)
- [ ] **Fenêtres ouvertes** : le sous-menu liste les 3 fenêtres + la courante (marquée `•`)
- [ ] **Focus au clic** : cliquer sur une autre fenêtre de la liste → elle passe au premier plan
- [ ] **Ouvrir une fenêtre → Fenêtre vide** (1ère option) → une 4e fenêtre vierge s'ouvre
- [ ] **Ouvrir → templates** (dashboard, agent IDE, etc.) → fenêtre avec le layout du template
- [ ] **Enregistrer la fenêtre** : modifie le layout, puis "Layout + thème" → sauvegardé (recharge = retrouvé)
- [ ] **Enregistrer → Layout seul** → sauvegarde sans le thème
- [ ] **Thème** : Sombre/Clair → le bouton répond (le style ne change pas encore, c'est attendu — non branché)
- [ ] **Choisir un layout** : liste les layouts (default/dashboard/agentIde/blank) → clic = la fenêtre courante change de layout

## 3. Fenêtre vierge
- [ ] Écran d'accueil « Utilisez le menu Panneaux → Catalogue »
- [ ] **Ajouter le 1er panel** → il devient la racine (pleine fenêtre)
- [ ] **Ajouter un 2e panel** → splitter vertical (2 panneaux empilés)
- [ ] **Ajouter un 3e panel** → s'empile en dessous
- [ ] **Ajouter un panel déjà présent** → pas de doublon (juste visible)

## 4. Menu Panneaux
- [ ] **De cette fenêtre** : liste les panels visibles avec ☑ (cochés)
- [ ] **Décocher un panel** → il disparaît de la fenêtre
- [ ] **Recocher** → il réapparaît
- [ ] **Ouverts dans une fenêtre** : sous-menu par fenêtre → les panels d'une autre fenêtre sont listés
- [ ] **Ajouter depuis une autre fenêtre** → le panel s'ajoute à la fenêtre courante
- [ ] **Catalogue (non ouvert)** : liste tous les panels triés, sans ceux déjà ouverts
- [ ] Les panels **essentiels ET externes** sont dans le catalogue

## 5. Vérifier TOUS les panneaux (un par un)

> Pour chacun : ouvrir dans une fenêtre vierge ou existante, vérifier qu'il se charge, rend sans erreur JS, et affiche du contenu cohérent avec son nom. Les **11 essentiels** sont dans le monolithe (chargés immédiatement) ; les **17 autres** en lazy-load (paresseux) — vérifier les deux cas.

### Installateur (4)
- [ ] `installator-dashboard` — Dashboard
- [ ] `installator-deps` — Dépendances
- [ ] `installator-file-queue` — File de téléchargement
- [ ] `installator-outils-installes` — Outils installés

### Système (4)
- [ ] `systeme-dashboard` — Dashboard
- [ ] `systeme-etat` — État système
- [ ] `systeme-ressources` — Ressources (CPU/RAM/disque)
- [ ] `systeme-llm-locaux` — LLM locaux

### Agents (5)
- [ ] `agents-liste` — Agents (essentiel)
- [ ] `agents-lanceur` — Lanceur
- [ ] `agents-monitoring` — Monitoring
- [ ] `agents-topologie` — Topologie
- [ ] `agents-composition-equipe` — Équipe

### Gestion (6)
- [ ] `gestion-panneaux` — Gestion des panneaux (essentiel)
- [ ] `gestion-bundles` — Bundles
- [ ] `gestion-catalogue-modeles` — Catalogue de modèles
- [ ] `gestion-cles-api` — Clés API
- [ ] `gestion-outils` — Outils Registry
- [ ] `docker-ressources` — Ressources Docker

### Monitoring/Debug (5)
- [ ] `monitoring/processus` — Processus
- [ ] `monitoring/projets` — Projets
- [ ] `monitoring/llm-distant` — Moniteur LLM distant
- [ ] `debug-logs` — Logs
- [ ] `debug-services` — Services

### Projet (2)
- [ ] `projet-equipes` — Équipes
- [ ] `projet-workspace` — Workspace

### Communication (1)
- [ ] `communication-chat` — Chat (essentiel)

### Sandbox (1)
- [ ] `sandbox-agent-ide` — Agent IDE (essentiel)

### Critères par panel
- [ ] Se charge sans écran "Chargement…" infini
- [ ] Rendu correct (pas d'erreur `Load failed` dans `gui.log`)
- [ ] Données affichées cohérentes (modèles, process, services, clés…)
- [ ] Fonctionne aussi bien **dans une fenêtre vierge** (ajout via Panneaux → Catalogue) que dans son layout d'origine

## 6. Panels externes (lazy-load)
- [ ] Charger un panel externe (ex. Debug) → s'affiche après un bref chargement
- [ ] **React partagé** : le panel externe rend sans erreur (utilise window.React via le daemon)
- [ ] **Compilation à chaud** : `POST /v1/panels/build` recompile un panel modifié → recharger la fenêtre → le panel à jour

## 7. Sync inter-fenêtres
- [ ] Ajouter un panel dans une fenêtre → l'autre fenêtre le voit dans « Ouverts dans une fenêtre »
- [ ] Fermer une fenêtre → elle disparaît de la liste des fenêtres des autres

## 8. Persistance (fermeture/relance)
- [ ] **Déplacer/redimensionner** une fenêtre puis la fermer → `~/.modelweaver/windows/<id>.json` contient la position/taille
- [ ] **Maximiser** puis fermer → état `maximized` persisté
- [ ] **Relancer la GUI** → la fenêtre rouvre à la position/taille sauvegardée
- [ ] **Layout modifié sauvegardé** → `~/.modelweaver/layouts/` contient la version

## 9. Boutons fenêtre
- [ ] **Plein écran** (menu ou F11) → bascule
- [ ] **Fermer** (menu Fenêtre → Fermer, ou Ctrl+W) → ferme la fenêtre, pas toute l'app

## 10. Divers
- [ ] **Full-log GUI** : `~/.modelweaver/gui.log` reçoit les événements (`broadcast_window_state`, `app:layout`…)
- [ ] **Rafraîchir le layout** (layout:save) → le menu se met à jour
- [ ] **Pas d'erreur console** JS bloquante dans les webviews
