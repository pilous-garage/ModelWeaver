# V2-script — sources de la GUI ModelWeaver V2 (à évaluer)

Copie des **fichiers sources uniquement** (hors générés : dist/, node_modules/,
target/, caches) pour une revue par IA. Ce dossier est un INSTANTANÉ de travail.

## Contexte

La GUI V2 (`interfaces/main/GUI/v2/` dans le repo réel) est une réécriture Tauri/React
de la GUI ModelWeaver. Elle est servie par un binaire Tauri (Rust) qui embarque le
frontend React (Vite + TypeScript) + charge des layouts YAML via un daemon HTTP
(POST `/v1/<route>`, réponse enveloppée `{ok, route, result}`).

## Architecture

- `src/main.tsx` : bootstrap (init panels, chargement langue/layout/theme par fenêtre)
- `src/App.tsx` : racine d'UNE fenêtre, résolution du layout + menu global, actions menu
- `src/layout/` : modèle de données pur (types, ops de mutation, resolve, persist YAML)
  - `types.ts` : Layout (split/group/**miniLayout**), MenuItem, Session
  - `ops.ts` : mutations pures (addPanel, moveTab, splitGroup, resizeSplit, addMiniLayout…)
  - `resolve.ts` : layout → structure de rendu + menu global
  - `persist.ts` : sérialisation YAML + persistance daemon (debounce)
- `src/components/` : rendu
  - `MenuBar.tsx` : barre de menu (état openPath/hoverPath)
  - `TabGroup.tsx` : groupe d'onglets + drag/drop (ghost, marqueurs)
  - `SplitTree.tsx` : rendu récursif + resize des séparateurs
  - `PanelBoundary.tsx` : ErrorBoundary par panel
- `src/dragStore.ts` + `src/dnd.ts` : drag & drop cross-group (registre global)
- `src/panels/` : contract PanelDef + ~30 panels V2 (migrés de V1, HTTP pur)
- `src/bridge.ts` : IPC Tauri + POST daemon (retry port dynamique)
- `src/i18n.ts` : multilingue FR/EN (store réactif + persistance)
- `src/theme.ts` : thèmes CSS vars (dark/light/oled/sepia)
- `src/windows.ts` : gestion multi-fenêtres (profils daemon + poll)
- `src/guiInspector.ts` : traducteur de fenêtre (inspect/act via daemon) pour tests
- `src-tauri/main.rs` + `tauri.conf.json` : binaire Tauri (create_window, daemon_config…)
- `test_gui_e2e.py` : script de test E2E (23 scénarios : drag/drop, resize, thème…)
- `prepare_docker.py` : préparateur de conteneurs Docker
- `SPEC.md` : spécification de référence
- `checklist-gui.md` / `todo-sleep.md` : validation + carnet de session

## Points d'attention demandés à la revue

1. **MenuBar** : comportement survol/surbrillance — fix en cours (le document mousedown
   hors items doit fermer le menu ; état openPath/hoverPath).
2. **Drag & drop cross-group** : dragStore (registre global de groupes) + mutations
   via ops (le layout est la source de vérité). Vérifier la robustesse.
3. **Mini-layout** (ex-slip view) : layout imbriqué avec onglet, switchable, menus.
4. **Persistance layout** : debounce frontend (250ms) + écriture atomique daemon.
5. **Port daemon dynamique** : CSP wildcard + retry dans bridge.ts.
6. **i18n FR/EN** : store réactif + langEmbedded/langEmbeddedEn sur les panels.

## Fichiers clés à lire en priorité

- `src/components/MenuBar.tsx`
- `src/components/TabGroup.tsx` + `src/components/SplitTree.tsx`
- `src/dragStore.ts` + `src/layout/ops.ts`
- `src/App.tsx` + `src/layout/resolve.ts`
- `src/panels/contract.ts` + un exemple de panel (`src/panels/debug-services.panel.tsx`)
- `src-tauri/main.rs` + `tauri.conf.json`

Note : certaines modifications de session peuvent être en cours (MenuBar). Le dossier
est une photographie, pas forcément un état buildable à l'identique (dépendances npm
non incluses).
