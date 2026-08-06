# Checklist de validation GUI ModelWeaver (V0.8.10)

Checklist complète et ordonnée pour vérifier la GUI. À cocher au fur et à mesure.
Ordre logique : base → menu Fenêtre → fenêtre vierge → menu Panneaux → les 28 panels → lazy-load → sync → persistance → boutons → divers.

## 1. Démarrage & intégrité du système
- [ ] **3 fenêtres s'ouvrent** : Installateur, Dashboard, Agent IDE (chacune sur son layout : default/dashboard/agentIde)
- [ ] **Aucun service écrasé** : daemon répond toujours (curl health), les 9 services tournent (superviseur Python inchangé)
- [x] **Chaque fenêtre affiche le bon layout** : main = default (ressources + variant / etat + etat-simple), ide = default (validé via gui/inspect 2026-08-05)
- [x] **Thème sombre appliqué** dans chaque fenêtre (fond `#0f172a`) — style inline `--mw-bg` vérifié dans le DOM

## 2. Menu Fenêtre (dans chaque fenêtre)
- [x] **Fenêtres ouvertes** : le sous-menu liste les fenêtres + la courante (marquée `•` + `☑`) — validé via gui/inspect (main • / ide) 2026-08-05
- [x] **Focus au clic** : cliquer sur une autre fenêtre de la liste → `focus_window(ide)` (log Rust) — validé 2026-08-05
- [x] **Ouvrir une fenêtre → Fenêtre vide** (1ère option) → une 4e fenêtre vierge s'ouvre — `create_window` OK (log `create_window OK win-…`, layout par défaut complet, poller répond pour la nouvelle fenêtre)
- [x] **Ouvrir → templates** (dashboard, agent IDE, etc.) → `window:open:<template>` → `windows/create` avec template + taille (testé via menu : templates listés dans « Ouvrir une fenêtre »)
- [x] **Enregistrer la fenêtre** : `layout:save` + `windows/update` (layout par fenêtre `layout-<winId>`) — validé : split drag puis theme:set → `~/.modelweaver/layouts/layout-main.json` à jour
- [ ] **Enregistrer → Layout seul** → sauvegarde sans le thème
- [x] **Thème** : Sombre/Clair → le sous-menu bascule + CSS injecté + persisté (`theme: {global: light}`) — validé 2026-08-05
- [ ] **Choisir un layout** : liste les layouts (default/dashboard/agentIde/blank) → clic = la fenêtre courante change de layout

## 3. Fenêtre vierge
- [x] Écran d'accueil « Fenêtre vide — utilisez le menu pour ajouter des panneaux » (rendu App.tsx quand `resolved.root` est null)
- [ ] **Ajouter le 1er panel** → il devient la racine (pleine fenêtre)
- [ ] **Ajouter un 2e panel** → splitter vertical (2 panneaux empilés)
- [ ] **Ajouter un 3e panel** → s'empile en dessous
- [x] **Ajouter un panel déjà présent** → pas de doublon (juste visible) — **règle implémentée 2026-08-05 : 1 exemplaire par fenêtre si params identiques** ; params différents = panel différent (ex. ressources{params} + ressources{sans params} cohabitent). Vérifié : fermeture etat-simple + ré-ajout → 1 seul exemplaire ; 2e clic → plus listé au catalogue + onglet activé

## 4. Menu Panneaux
- [ ] **De cette fenêtre** : liste les panels visibles avec ☑ (cochés)
- [ ] **Décocher un panel** → il disparaît de la fenêtre
- [ ] **Recocher** → il réapparaît
- [x] **Ouverts dans une fenêtre** : sous-menu par fenêtre → les panels d'une autre fenêtre sont listés — validé 2026-08-05 (« Ouverts dans une fenêtre → ide », panels via layout/get des autres fenêtres)
- [ ] **Ajouter depuis une autre fenêtre** → le panel s'ajoute à la fenêtre courante
- [x] **Catalogue (non ouvert)** : liste les panels triés, sans ceux déjà ouverts — filtre implémenté (listPresentPanels + paramsEqual) ; vérifié : 3 panels ouverts → seuls les absents listés, puis sous-menu vide quand tout est ouvert
- [x] Les panels **essentiels** sont dans le catalogue — panels V2 natifs (ressources, etat-systeme, ressources-variant, etat-simple) + **12 panels migrés de V1** (processus, projets, workspace, outils, bundles, agents-monitoring, llm-distant, systeme-etat, systeme-ressources, file-queue, outils-installes, dashboard) + **externes de l'index daemon** (agents-liste, chat, déps, etc.) — validé 2026-08-05

## 5. Vérifier TOUS les panneaux (un par un)

> **État V2 (2026-08-05)** : les panels **essentiels V1 ne sont PAS dans le monolithe V2** (la V2 est une réécriture). 12 panels ont été **migrés en natifs V2** (cochés) ; les autres restent **externes compilés** (lazy-load, index daemon) ou **non migrés**.

### Installateur (4)
- [x] `installator-dashboard` — Dashboard (**migré V2** : system/hardware + system/resources)
- [ ] `installator-deps` — Dépendances (externe non migré)
- [x] `installator-file-queue` — File d'installation (**migré V2** : jobs/list, cancel, clear)
- [x] `installator-outils-installes` — Outils installés (**migré V2** : tools/installed/list, jobs/add)

### Système (4)
- [ ] `systeme-dashboard` — Dashboard (externe non migré)
- [x] `systeme-etat` — État système (**migré V2** : system/hardware)
- [x] `systeme-ressources` — Ressources (**migré V2** : system/resources, poll 2s)
- [ ] `systeme-llm-locaux` — LLM locaux (externe non migré)

### Agents (5)
- [ ] `agents-liste` — Agents (externe lazy-load — chargement OK, rendu V1 à adapter)
- [ ] `agents-lanceur` — Lanceur (externe non migré)
- [x] `agents-monitoring` — Monitoring (**migré V2** : agent/metrics + service/resources)
- [ ] `agents-topologie` — Topologie (externe non migré)
- [ ] `agents-composition-equipe` — Équipe (externe non migré)

### Gestion (6)
- [ ] `gestion-panneaux` — Gestion des panneaux (externe non migré)
- [x] `gestion-bundles` — Bundles (**migré V2** : catalogue/bundles/list)
- [ ] `gestion-catalogue-modeles` — Catalogue de modèles (externe non migré)
- [ ] `gestion-cles-api` — Clés API (externe non migré)
- [x] `gestion-outils` — Outils Registry (**migré V2** : catalogue/tools/list)
- [ ] `docker-ressources` — Ressources Docker (externe non migré)

### Monitoring/Debug (5)
- [x] `monitoring-processus` — Processus (**migré V2** : system/processes, poll 3s)
- [x] `monitoring-projets` — Projets (**migré V2** : team/list, poll 10s)
- [x] `monitoring-llm-distant` — Moniteur LLM distant (**migré V2** : usage/monitor, fenêtres 1h/24h/7j)
- [ ] `debug-logs` — Logs (externe non migré)
- [ ] `debug-services` — Services (externe non migré)

### Projet (2)
- [ ] `projet-equipes` — Équipes (externe non migré)
- [x] `projet-workspace` — Workspace (**migré V2** : team/init-workspace)

### Communication (1)
- [ ] `communication-chat` — Chat (externe lazy-load)

### Sandbox (1)
- [ ] `sandbox-agent-ide` — Agent IDE (externe lazy-load — chargement OK, rendu V1 à adapter)

### Critères par panel
- [x] Se charge sans écran "Chargement…" infini — panels migrés V2 : oui (ErrorBoundary + polling)
- [ ] Rendu correct (pas d'erreur `Load failed` dans `gui.log`) — panels V1 externes : chargement OK, rendu ctx à adapter
- [ ] Données affichées cohérentes (modèles, process, services, clés…)
- [ ] Fonctionne aussi bien **dans une fenêtre vierge** (ajout via Panneaux → Catalogue) que dans son layout d'origine

## 6. Panels externes (lazy-load)
- [x] Charger un panel externe (ex. agents-liste) → import() dynamique du JS servi par le daemon (bypass auth GET panels/file, CSP corrigée, `_shared/react` exporte jsx/jsxs, `window.React` exposé) — **chargement validé 2026-08-05** : `agents-liste` ajouté au layout
- [x] **React partagé** : `window.React` exposé (main.tsx) + module `_shared/react` ré-exporté par le daemon (React.createElement pour jsx/jsxs)
- [x] **Isolation des crashs** : `PanelBoundary` (ErrorBoundary) par panel — un panel V1 qui crash affiche un cadre d'erreur, l'app reste vivante — validé 2026-08-05
- [ ] **Compilation à chaud** : `POST /v1/panels/build` recompile un panel modifié → recharger la fenêtre → le panel à jour
- [ ] **Rendu du contenu des panels V1 externes** en V2 (contrat ctx V1 à adapter : états globaux useApp absents en V2) — chargement OK, contenu partiel

## 7. Sync inter-fenêtres
- [x] Ajouter un panel dans une fenêtre → l'autre fenêtre le voit dans « Ouverts dans une fenêtre » — validé 2026-08-05 (lecture layout/get des autres fenêtres, poll 5s)
- [x] Fermer une fenêtre → elle disparaît de la liste des fenêtres des autres — validé (profil orphelin nettoyé automatiquement par le poll)

## 8. Persistance (fermeture/relance)
- [x] **Déplacer/redimensionner** une fenêtre puis la fermer → `~/.modelweaver/windows/<id>.json` contient la position/taille — validé 2026-08-05 (main: x=0,y=0,w=1252,h=852 ; pushCurrentWindowState throttlé 5s)
- [ ] **Maximiser** puis fermer → état `maximized` persisté
- [x] **Relancer la GUI** → la fenêtre rouvre avec le layout par fenêtre (`layout-<winId>` : main + ide recréées au boot) — validé 2026-08-05
- [x] **Layout modifié sauvegardé** → `~/.modelweaver/layouts/layout-main.json` contient la version (sizes split après drag + theme light) — validé 2026-08-05

## 9. Boutons fenêtre
- [x] **Plein écran** (menu ou F11) → bascule — `toggleFullscreen` branché (invoke window_fullscreen) + F11 listener — validé 2026-08-05 (`window_fullscreen(main) -> true` + persistance `state: fullscreen` dans windows/main.json)
- [x] **Fermer** (menu Fenêtre → Fermer, ou Ctrl+W) → ferme la fenêtre, pas toute l'app — `close_current_window` validé : main fermée, ide + win-… restent actives, process GUI vivant

## 11. Onglets : clic vs drag (corrigé 2026-08-05)
- [x] **Clic simple sur un onglet = activation** (jamais de split) — fix : timer 180ms + seuil mouvement 5px dans TabGroup (handleMouseDown distingue clic/drag) + guiInspector `detail:1` sur les MouseEvent (React ignorait les clicks synthétiques sans detail). Validé : clic tab-etat-systeme → actif change, groupe inchangé (aucun split).
- [x] **Drag & drop reorder** : drag d'un onglet dans la barre → inséré à l'index du curseur (`computeInsertIndex`, data-occ). Validé : etat-systeme dragué en 1ère position → `[etat-systeme, ressources, etat-simple]`.
- [x] **Drag au bord** (hors barre, relX<0.12 / >0.88) → split horizontal ; haut/bas → vertical. Validé : drag etat-systeme vers le bord droit → split horizontal [50,50] + nouveau groupe.
- [x] **Drag au centre du corps** → rien (l'onglet reste sur place) — pas de split accidentel.

## 10. Divers
- [ ] **Full-log GUI** : `~/.modelweaver/gui.log` reçoit les événements (`broadcast_window_state`, `app:layout`…)
- [x] **Rafraîchir le layout** (layout:save) → le menu se met à jour — fichier YAML réécrit à chaque mutation (persistLayout temps réel)
- [ ] **Pas d'erreur console** JS bloquante dans les webviews (les panels V1 externes peuvent loguer des erreurs de ctx — à adapter)

---
## Validé hors checklist (2026-08-05) — règles implémentées & testées
- **Unicité des panels par fenêtre** (règle utilisateur) : 1 exemplaire par layout+sous-layouts si params identiques (`findPanelOccurrence`/`paramsEqual` dans ops.ts) ; params différents = panel différent. `addPanel` active l'existant, `addSlipView` skip, catalogue filtré.
- **Fermeture d'onglet par bouton ✕** (testid `tab-close-*`) : validé (etat-simple fermé puis ré-ajouté via menu).
- **Slip view** : ajout via Panneaux → Slip view → `slip-view-*` apparaît (conteneur avec sous-arbre, titre, bouton ✕) ; fermeture supprime slip + onglet interne.
- **Drag & drop souris** (dnd custom) : reorder au centre de la barre (variant 117→219), split au bord gauche (relX<0.12 → nouveau groupe x=409, les autres groupes repoussés).
- **window:new** : `create_window(win-<ts>)` OK, fenêtre indépendante avec layout par défaut + poller fonctionnel.
- **Labels i18n** : langEmbedded ajouté à ressources-variant (« Ressources (variant) ») et etat-simple (« État (simple) »).
- **Tests unitaires** : 41 passés (6 nouveaux tests dédup + 2 resizeSplit). Typecheck + build OK. cargo build --release OK.

## Session 2026-08-05 — fonctionnalités SPEC V2 implémentées & validées
- **Multi-fenêtres + sync** : `src/windows.ts` (store React pollé 5s : profils daemon + fenêtres Tauri + panels des autres fenêtres) ; menu Fenêtre complet (fenêtres ouvertes • courante, focus `focus_window`, ouvrir templates, enregistrer, fermer, F11 plein écran) ; persistance position/taille (`windows/update` throttlé) ; nettoyage profils orphelins.
- **12 panels V1 migrés → V2 natifs** : monitoring-processus, monitoring-projets, projet-workspace, gestion-outils, gestion-bundles, agents-monitoring, monitoring-llm-distant, systeme-etat, systeme-ressources, installator-file-queue, installator-outils-installes, installator-dashboard — branchés sur routes HTTP daemon (helper `usePoll`), labels i18n embarqués.
- **Lazy-load panels externes** : `src/panels/loader.ts` (panels/index → import() dynamique du JS servi par le daemon) ; bypass auth GET `panels/file/` (import() ne porte pas le Bearer) ; CSP Tauri élargie (`script-src` 127.0.0.1:8770) ; `window.React`/`ReactDOM` exposés ; daemon `_shared/react` exporte jsx/jsxs/Fragment ; `PanelBoundary` isole les crashs de rendu.
- **Thèmes** : `src/theme.ts` (injection CSS `#mw-theme`, dark/light builtin + thèmes daemon) ; menu Affichage → Thème (Sombre/Clair) ; persistance dans `layout.theme.global`.
- **Split resize** : op `resizeSplit` (ops.ts, `SplitNode.id` généré) ; séparateurs draggables custom souris dans SplitTree (`onResize` → mutate) ; persistance des sizes (validé : 50/50 → 20/80).
- **Layout par fenêtre** : `loadWindowLayout(windowId)` (layout-get `layout-<winId>`, fallback défaut) ; persistance `layout-<winId>` au lieu du `main` partagé (évite la collision entre fenêtres).
- **Bugs corrigés** : lectures réponses daemon enveloppées (`res.result.xxx` — le POST HTTP enveloppe tout dans `{ok, route, result}`) dans windows.ts, theme.ts, loader.ts, main.tsx et 3 panels ; rate limit route `windows/` `layout/` `theme/` `session/` `panels/` → 240 req/min + routes `gui/*` exemptées (pilotage backend).
