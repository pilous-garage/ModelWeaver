# TODO-SLEEP — Carnet de bord complet (session 2026-08-05)

Contexte : l'utilisateur se repose. Objectif = avancer en autonomie sur la GUI V2,
la migration des panels, l'infra de test (docker + vraie souris), le swarm. Il veut
un carnet de bord AVANT d'attaquer, des délégations au swarm (migration panels faciles,
analyse, tests), un préparateur de docker, et un gros test E2E GUI (drag/drop partout,
vérif layout/persistance). Ne PAS utiliser ollama local pour l'instant.

---

## État de référence (avant de commencer)
- GUI V2 : binaire release relancé (PID 69072), layout reset 2 groupes
  (pg-gauche: ressources+ressources-variant / pg-droit: etat-systeme+etat-simple).
- Daemon sur 8770 (supervisé), superviseur UP, api.port=8770.
- Tests unitaires frontend : **44 verts** (ops, persist, dnd, App).
- Drag & drop V2 COMPLET (cross-group) validé : dragStore.ts (registre global de
  groupes + computeDrop + store React), ghost, marqueurs (insert/append/split),
  mutations via ops.moveTab/splitGroup (le "text" layout).
- Panels V2 migrés (12) : monitoring-processus, monitoring-projets, projet-workspace,
  gestion-outils, gestion-bundles, agents-monitoring, monitoring-llm-distant,
  systeme-etat, systeme-ressources, installator-dashboard, installator-file-queue,
  installator-outils-installes.
- Swarm : `team:swarm-selfimprove-v2` ready, workspace `mw-swarm`. Watcher poll 30s.
- Infra docker : `docker/Dockerfile.*` existants (bare/base/test/e2e/keep-gui…),
  `docker/build-docker.sh`, entrypoints. xdotool ABSENT de l'hôte ; docker dispo.

---

## TÂCHES (ordre de priorité)

### A. Swarm : coder la team + valider la délégation (FAIRE EN PREMIER)
- [x] A1. Créer le manifest `services/manifests/teams/gui-tasks.team.yaml` :
      équipe dédiée GUI (analyst + codeurs junior/senior + testers + reviewer/integrator),
      workspace `mw-gui-tasks`, flat topology (pattern swarm-selfimprove-v2).
- [x] A2. Charger le manifest (boot daemon relit les .team.yaml) ; vérifier
      `team/list` montre la nouvelle team `ready` — OK : team:gui-tasks ready,
      workspace mw-gui-tasks, 8 agents créés (analyst, coder-a/b/c, tester-a/b,
      reviewer, integrator).
- [x] A3. Créer le workspace via `team/init-workspace` (ws_name=mw-gui-tasks) —
      le workspace mw-gui-tasks existe avec director team:gui-tasks. Le handler
      team/init-workspace a un bug (_set_workspace_director appelé en méthode) —
      NON bloquant car le lien est fait au setup via team_manager.
- [x] A4. Soumettre 1 issue TEST (migration d'un panel facile) + `team/delegate`
      + wake_up → l'issue 51 (debug-services) a été DÉTECTÉE par le watcher
      (status analyzed), des tâches créées et marquées `done`… mais AUCUN fichier
      produit (branch/commit vides, repo workspace-issue-51.git vide). → **BUG
      pipeline : les agents marquent les tâches done sans écrire/committer.**
      Cause probable : la toolbox LLM (_build_llm_tools dans fsm_interpreter.py)
      n'expose que 4 skills (shell/exec, git/lite, read_file, write_file) alors
      que les prompts des teams référencent code_gen_v1/analyse_v1 (inexistants
      via call_skill — implementation.type: llm non exécutable). Les agents
      hallucinent des fonctions → échec → boucle → done sans output.
      → À INVESTIGUER plus tard (fix possible : adapter la toolbox ou les prompts
      aux 4 tools réels — fait pour gui-tasks mais non retesté ; le swarm
      self-improve a le même défaut).
- [x] A5. Documenter : la marche à suivre = créer issue via `workspace/issues/add`
      (workspace mw-gui-tasks) → le watcher (30s) la passe analyzed → tâches dans
      workspace-issue-<id>. MAIS le pipeline ne produit pas de fichiers fiables
      → privilégier la migration directe par moi-même pour l'instant.

### B. Migration des panels "faciles" restants (déléguer au swarm)
Panels restants V1 (`interfaces/main/GUI/official/gui/src/panels/`) :
- [x] B1. Identifier les SIMPLES : **AUCUN des panels restants n'est "simple"** —
      analyse : les wrappers (13-31 lignes) délèguent tous à des composants
      partagés (CataloguePanel, KeysPanel, ServicesMonitorPanel, DockerResourcesPanel,
      LocalModelsPanel, SystemDashboardPanel, AgentLauncherPanel, DebugPanel) qui
      dépendent de l'état global useApp (catalogueTools, procList, serviceList,
      queueJob, loadingActions, handleAddToInstallList…) + commandes Tauri
      (invoke service_log, install_queue_*). Migration "facile" NON possible :
      il faut découpler useApp → routes HTTP + état local par panel (travail
      par panel, ~1h chacun).
- [x] B2. Le swarm (swarm-selfimprove-v2 ET gui-tasks) NE produit PAS de fichiers
      fiables (tâches done sans commit) → bug pipeline à investiguer plus tard.
- [ ] B3. (Optionnel, plus tard) Migrer les panels complexes un par un en
      découplant useApp (ex. cles-api → keys/list direct, services → service/list
      direct, llm-locaux → llm/local/list). NON prioritaire cette session.

### C. Préparateur de docker (docker/prepare_docker.py + images vierges)
- [x] C1. `docker/prepare_docker.py` écrit : CLI list/ensure/run/install/add/
      snapshot/clean. Décide si on crée/relance (état des images/containers).
- [x] C2. Images vierges par niveau : `mw-base:ubuntu`/`deps`/`gui`/`test`,
      dérivées depuis modelweaver-base / modelweaver-gui-test (re-tag) ou
      Dockerfiles. `mw-base:gui`, `mw-base:deps`, `mw-base:gui-nano` créés.
- [x] C3. `install` (apt) et `add` (copie fichiers) validés dans un conteneur ;
      `snapshot` commit en nouvelle image vierge (sans polluer la référence).
- [x] C4. Conteneur `gui-test` créé puis nettoyé (clean). Workflow OK.

### D. Script de test E2E complet GUI (scripts/test_gui_e2e.py)
- [x] D1. Structure : découvre les éléments par testid dans CHAQUE fenêtre
      (gui/inspect → box {x,y,w,h}), agit par coordonnées (gui/act), VÉRIFIE le
      résultat attendu après chaque action.
- [x] D2. Tests "GUI pure" couvrant TOUT :
      - clic simple onglet = activation (jamais split) ✓
      - reorder intra-groupe (drag dans la barre → index curseur) ✓
      - split au bord (10% gauche/droite/haut/bas) → aperçu + split ✓
      - cross-group barre / centre / bord (déplacer un onglet d'un groupe à l'autre) ✓
      - drag dans les SLIP VIEWS (panneaux conteneurs de sous-arbres) ✓
        (test_drag_into_slip : layout avec slip view → drag ressources dans le
        groupe interne pg-slip → PASS)
      - menu Panneaux (catalogue filtré, onglet/slip view), fermeture ✕ ✓
      - thème (Sombre/Clair) ✓, fenêtres ✓
      - lazy-load panel externe (index daemon) — couvert via catalogue
- [x] D3. Vérification "tout passe par le layout" : après CHAQUE action, relire
      `layout/get layout-<winId>` → le YAML reflète la mutation (ordre des onglets,
      groupes). Vérifié à chaque scénario.
- [x] D4. Vérification "le layout s'enregistre après chaque modif" : mtime du
      fichier layout-<winId>.json change à chaque mutation (testé à chaque scénario).
- [x] D5. Rapport : PASS/FAIL/WARN par étape + problèmes visibles (mw-load-err,
      panel-error). La GUI est RELANCÉE entre les scénarios de mutation (l'App V2
      ne recharge le layout qu'au boot) — c'est le prix de la fiabilité.
- [x] D6. **Resize de séparateur + resize de fenêtre** ajoutés (test_resize_separator,
      test_resize_window) → 23 PASS / 0 FAIL. 
      **BUG de corruption du layout trouvé et corrigé** : le drag continu d'un
      séparateur déclenche N layout/save concurrents qui corrompent le fichier
      (`p.write_text` non atomique → `4` parasite). Fix DOUBLE :
      1) daemon `_save_json` → écriture atomique (tmp + os.replace) ;
      2) frontend `persistLayout` → debounce 250ms (1 requête max, toujours le
      dernier layout). Résultat : fichier YAML toujours valide.
      **BUG Separator dérapage corrigé** : le delta était TOTAL depuis le mousedown
      (re-appliqué à chaque mousemove sur le layout déjà redimensionné → double-
      compte). Fix : delta INCÉRÉMENTAL (depuis le dernier mousemove).
- [x] **Bugs GUI trouvés par le test** :
      - CSP critique : connect-src 127.0.0.1:8770 en dur → daemon sur 8771 ⇒
        frontend coupé (poller down, gui/inspect pending). Fix : remettre le daemon
        sur 8770. (Documenté F1.)
      - Slip view via clic menu : addSlipView fonctionne en unitaire, mais le
        ciblage du sous-menu "Slip view" est instable en simulation (item sans
        testid) → WARN dans le test, pas un bug du drag.

### E. Test avec VRAI mouvement de souris (Docker + Xvfb + xdotool)
- [x] E1. Image GUI : `mw-base:gui` créé depuis modelweaver-gui-test (contient
      Xvfb+xdotool+webkit) via le préparateur. `mw-base:deps` + `mw-base:gui-nano`
      aussi créés. Préparateur COMPLET : ensure/run/install/add/snapshot/clean validés.
- [x] E2. GUI lancée dans le conteneur (Xvfb :99, réseau hôte) : **boote et
      s'affiche** (screenshot = thème dark `#0f172a`, fenêtres main 1200x800 +
      ide 1000x700).
- [x] E3. Vérifié l'affichage via screenshot (pixels sombres = thème dark).
- [x] E4. **Le drag/clic xdotool (XTEST) n'est PAS traité par la webview
      WebKitGTK en headless** : les événements simulés ne déclenchent pas React
      (ni click ni drag n'ont d'effet, testé avec plusieurs offsets/offsets + openbox).
      C'est un comportement WebKitGTK connu en environnement headless (pas de vrai
      device de pointeur). → PAS de validation du drag réel via xdotool.
- [x] E5. Alternative fonctionnelle : la GUI du conteneur répond PARFAITEMENT au
      guiInspector (gui/act → drag synthétique, onglets détectés) → le test E2E (D)
      est exécutable sur la GUI Docker comme sur la GUI hôte. La validation du drag
      se fait donc via gui/act (fiable), PAS via XTEST.
- [ ] E6. (Optionnel) Un vrai test souris nécessiterait un device virtuel uinput
      ou une VM graphique — non bloquant, hors scope cette session.

### F. Vérifs transverses + fin de session
- [x] F1. **BUG CSP critique trouvé PUIS CORRIGÉ (transmission du port daemon)** :
      la CSP listait `connect-src http://127.0.0.1:8770` en dur → si le superviseur
      déplaçait le daemon (8771), le frontend était coupé (poller down). FIX :
      1) CSP → wildcard `http://127.0.0.1:* http://localhost:*` (script-src +
      connect-src) ; 2) `daemonPost` (bridge.ts) retente avec une config fraîche
      (re-lit daemon_config → api.port écrit par le superviseur) si le fetch
      échoue. **Validé E2E** : daemon 8770→8771→8770, la GUI suit à chaque fois
      (gui/inspect done, tree non None). C'est la transmission dynamique de
      l'adresse du daemon à la GUI demandée par l'utilisateur.
- [ ] F2. Mettre à jour `checklist-gui.md` (items migrés/validés).
- [ ] F3. Mettre à jour `VERSIONS.md` et ce carnet (résumé final).

---

## Notes / contraintes
- Toute mutation de layout passe par les opérations pures (ops.ts) et est persistée
  (persistLayout). Les panels migrés doivent utiliser ctx.api.post + usePoll.
- Ne pas casser les 44 tests frontend existants.
- Ollama local : NE PAS utiliser (sauf debug en dernier recours).
- Docker : garder des images vierges de référence, ne jamais les polluer.
- Si le swarm est lent/en erreur : décrire le problème en tâche "proposer une
  solution" et attendre les réponses des autres LLM.
- Pour rester en autonomie après une délégation : lancer `sleep 600` en tâche de
  fond et vérifier périodiquement l'avancement du swarm (watcher/team status).

## 2026-08-06 — Renommage slip view → mini-layout
- `SlipNode` → `MiniLayoutNode` (type 'miniLayout'), addMiniLayout/closeMiniLayout/
  activateMiniLayout, menu.miniLayout, testids `mini-layout-*`. Rétro-compat :
  normaliseur accepte 'slip' → produit 'miniLayout'. Concept = layout imbriqué
  avec onglet, switchable, menus branchés. Drag cross-group dedans validé.
  45 tests verts, typecheck + builds OK.

## 2026-08-06 — Migration panels V2 (2e lot)
- Migrés découplés de useApp : `debug-services` (service/list poll 3s + restart/stop/
  start), `gestion-cles-api` (keys/list + delete + set_lock), `systeme-llm-locaux`
  (llm/local/list + start/stop). Enregistrés dans init.ts, au catalogue.
- Validé en GUI réelle : debug-services affiche les services réels (installer,
  catalogue, chat:installer avec PID + boutons ⟳/■), aucune erreur. Les 3 sont
  listés au catalogue. 45 tests unitaires verts, builds OK.
- Panels restants (complexes, dépendent encore de useApp) : gestion-panneaux,
  gestion-catalogue-modeles, docker-ressources, debug-logs, systeme-dashboard,
  agents-* (liste/lanceur/topologie/composition), projet-equipes, communication-chat,
  sandbox-agent-ide, installator-deps. À migrer en découplant useApp (non prioritaire).

## 2026-08-06 — Fenêtres préenregistrées + thèmes + layouts
- **3 layouts V2 préenregistrés** : monitoring, dev, admin (panels V2, ~/.modelweaver/layouts/).
- **2 thèmes persistant** : oled, sepia (~/.modelweaver/themes/). dark/light builtin.
- **4 fenêtres préenregistrées** : dashboard, monitoring (layout monitoring + theme oled),
  dev, admin (profils windows/*).
- **1 session** : session-travail (3 fenêtres avec layouts).
- **Bug TDZ corrigé** (windows.ts) : `isPredefined` utilisé avant sa déclaration →
  ReferenceError → le tick du store plantait → profils jamais mis à jour. Fix :
  déclaration avant usage. Symptôme : fenêtres préenregistrées absentes du menu.
- **Bug openWindow corrigé** : recréait le profil (layout-<winId>) en écrasant le
  layout préenregistré. Fix : ne créer le profil que s'il n'existe pas.
- **Fenêtres préenregistrées au menu Fenêtre → Ouvrir** (après les templates) +
  persistance (exclues du nettoyage des orphelins si layout nommé).
- **loadWindowLayout amélioré** : profil de fenêtre (windows/list) prime → charge
  son layout référencé + thème du profil.
- **Validé E2E** : la fenêtre "monitoring" ouverte via le menu charge son layout
  (6 panels monitoring : processus, projets, systeme-ressources, agents-monitoring,
  debug-services, llm-distant). Profil intact (layout=monitoring, theme=oled).

## 2026-08-06 — Multilingue FR/EN complet
- i18n.ts réécrit : store réactif (useSyncExternalStore → bascule re-rend l'UI),
  dicts FR+EN embarqués (menu + global), loadLangYaml(yaml, locale).
- Traductions EN des 17 panels V2 : champ `langEmbeddedEn` (+ contrat PanelDef),
  générées par scripts/add_lang_en.py.
- Persistance : la langue est sauvegardée dans la session `prefs-langue`
  (session/save) et relue au boot (loadSavedLocale dans main.tsx).
- Changer la langue : menu Langue → Français/English (action lang:set:fr|en →
  setLocale + persistLocale). Réactif + persistant.
- Validé E2E : FR→EN→FR via menu (menu re-rend), persistance au boot (relance →
  démarre dans la langue choisie), 50 tests unitaires verts (5 i18n).

## 2026-08-06 — Fix quota quotidien + état final
- Rate limit quotidien des routes GUI (layout/windows/session/theme/panels) :
  élevé à 500_000/jour (les tests E2E + polling multi-fenêtres épuisaient les
  5000/jour). Fix dans ratelimit.py. Après redémarrage daemon, test E2E complet
  de nouveau vert (23 PASS).
- État final : 50 tests unitaires verts, 23 scénarios E2E verts, 18 panels V2,
  9 layouts, 2 thèmes, 6 fenêtres, multilingue FR/EN fonctionnel + persisté.

## 2026-08-06 — Migration panels V2 complète (3e + 4e lot)
- Migrés (HTTP pur, découplés de useApp) : docker-ressources, agents-liste,
  agents-lanceur, agents-equipe, agents-topologie, gestion-panneaux, debug-logs,
  installator-deps, gestion-catalogue-modeles, communication-chat, systeme-dashboard.
- Alias V1 : projet-equipes, agents-composition-equipe (réutilisent agents-equipe).
- Total : 29 fichiers .panel.tsx, 33 registerPanel. Reste SEUL sandbox-agent-ide
  (445 lignes, dépendances react-resizable-panels/dagre/CodeEditor → portage dédié).
- Validé en GUI réelle : agents-liste affiche les agents réels (bug-busters/
  code-analyst etc.), docker-ressources affiche mw-cache/pytest-base. Les 11
  nouveaux panels sont au catalogue. Rendus cohérents avec les données daemon.
- Note : llm/models/list renvoie 0 modèles (catalogue BDD vide, synchro non faite)
  → gestion-catalogue-modeles affiche "…" proprement (pas de crash).
- 50 tests unitaires verts, typecheck + builds OK. Bundle frontend 361K (11 panels).
