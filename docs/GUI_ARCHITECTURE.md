# GUI Architecture — Layouts, Sessions, Thèmes, Extensions

## Principes généraux

L'interface ModelWeaver est **un client HTTP** qui parle au daemon local
(`127.0.0.1:8770`). Toute l'interaction applicative passe par l'API REST.

Le GUI est une application Tauri (Rust + React). Les composants sont compilés
dans le binaire, mais leur **assemblage** (quels panneaux, où, dans quelles
fenêtres, avec quel menu, quel thème) est entièrement déclaratif et chargé
à runtime depuis des fichiers JSON dans `~/.modelweaver/`.

---

## 1. Cinq couches

```
┌─────────────────────────────────────────────────────────┐
│                    Session (multi-fenêtre)               │
│  ┌─────────────────┐  ┌─────────────────┐              │
│  │   Fenêtre 1     │  │   Fenêtre 2     │   ...        │
│  │  ┌───────────┐  │  │  ┌───────────┐  │              │
│  │  │  Layout   │  │  │  │  Layout   │  │              │
│  │  │ ┌──┬────┐ │  │  │  │ ┌──┬────┐ │  │              │
│  │  │ │P1 │ P2 │ │  │  │  │ │P3 │ P4 │ │              │
│  │  │ └──┴────┘ │  │  │  │ └──┴────┘ │  │              │
│  │  └───────────┘  │  │  └───────────┘  │              │
│  │  Thème: dark    │  │  Thème: light   │              │
│  └─────────────────┘  └─────────────────┘              │
└─────────────────────────────────────────────────────────┘
```

| Couche | Fichier | Description |
|--------|---------|-------------|
| **Panel** | `src/panels/**/*.panel.tsx` | Composant atomique. Parle au daemon via `daemonPost()`. |
| **Layout** | `~/.modelweaver/layouts/{id}.json` | Arbre de `react-resizable-panels` + panneaux + menu + thème |
| **Fenêtre** | Tauri `tauri.conf.json` + Rust `WindowBuilder` | Brique OS (label, taille, URL). Associe un layout. |
| **Session** | `~/.modelweaver/sessions/{id}.json` | Liste de fenêtres → leurs layouts |
| **Extension** | `~/.modelweaver/extensions/{name}/` | Panel externe chargé dans un iframe |

---

## 2. Organisation des panels

### 2.1 Arborescence

```
src/panels/
├── index.ts                     # PANEL_REGISTRY — généré automatiquement
├── Installator/
│   ├── catalogue-outils.panel.tsx
│   ├── file-installation.panel.tsx
│   └── install-queue.panel.tsx
├── Agents/
│   ├── liste.panel.tsx
│   ├── monitoring.panel.tsx
│   └── composition-equipe.panel.tsx
├── Communication/
│   └── chat.panel.tsx
├── Gestion/
│   ├── bundles.panel.tsx
│   ├── outils.panel.tsx
│   ├── permissions.panel.tsx
│   ├── sessions.panel.tsx
│   └── themes.panel.tsx
├── Systeme/
│   ├── cles-api.panel.tsx
│   ├── llm-locaux.panel.tsx
│   └── catalogue-modeles.panel.tsx
├── Debug/
│   ├── logs.panel.tsx
│   └── services.panel.tsx
├── Projet/
│   ├── workspace.panel.tsx
│   └── equipes.panel.tsx
└── Sandbox/
    ├── agent-ide.panel.tsx
    └── agent-ide/                  # sous-composants du panneau
        ├── CatalogTree.tsx
        └── CodeEditor.tsx

### 2.2 Règle d'identifiant

L'`id` d'un panneau doit correspondre à son chemin relatif :
- `src/panels/Installator/catalogue-outils.panel.tsx` → `id: "installator-catalogue-outils"`
- `src/panels/Agents/monitoring.panel.tsx` → `id: "agents-monitoring"`

Si un panneau a volontairement un `id` différent, il peut le signaler
avec `idWarning: false` (ou `idWarning: "raison"`) pour éviter le warning.

### 2.3 Build — Découverte automatique des panels

Le script `scripts/discover-panels.ts` (ou une étape du build Vite) :

1. Parcourt récursivement `src/panels/` à la recherche de `*.panel.tsx`
2. Pour chaque fichier, calcule l'`id` attendu depuis le chemin
3. Importe le fichier et vérifie son `PanelDef.id`
4. Si `id !== idAttendu` et `idWarning !== false` → `console.warn`
5. Vérifie l'unicité de tous les `id` → `console.error` + exit 1 si conflit
6. Génère `src/panels/index.ts` avec le `PANEL_REGISTRY`

### 2.4 Contrat PanelDef

Chaque panneau doit exporter un objet `PanelDef` (détaillé dans
`docs/PANEL_CONTRACT.md`). Résumé :

```typescript
export const Panel: PanelDef = {
  id: "agents-liste",                  // identifiant unique
  label: "Agents",                     // nom affiché
  icon: "smart_toy",                   // Material icon
  version: "1.0.0",
  description: "Liste et contrôle des agents",
  idWarning: false,                    // optionnel, supprime le warning si id≠chemin

  daemonRoutes: [
    { route: "agent/list", methods: ["GET"], desc: "Liste les agents" },
  ],
  menu: [
    { menuPath: ["Fichier"], label: "Nouvel agent", action: "panel:agents:new" },
  ],

  declaration(): string { /* retourne un résumé texte */ },
  onActivate(ctx): void { /* démarrer le polling */ },
  onDeactivate(ctx): void { /* arrêter le polling */ },
  onRefresh(ctx): Promise<void> { /* recharger les données */ },

  component: React.FC<PanelProps>,
};
```
        └── CodeEditor.tsx
```

---

## 2. Layout — Agencement d'une fenêtre

### Stockage

```
~/.modelweaver/layouts/
├── default.json       ← créé au premier lancement
├── dashboard.json
├── sandbox.json
└── mes-panneaux.json  ← créé par l'utilisateur
```

### Format

```json
{
  "$schema": "https://modelweaver.dev/schemas/layout.json",
  "id": "dashboard",
  "label": "Tableau de bord",
  "icon": "dashboard",
  "theme": "dark",

  "menu": [
    {
      "id": "workspace",
      "label": "Espace de travail",
      "items": [
        { "id": "new-window", "label": "Nouvelle fenêtre…", "shortcut": "Ctrl+N",
          "action": "window:select-layout" },
        { "id": "close-window", "label": "Fermer la fenêtre", "shortcut": "Ctrl+W",
          "action": "window:close" },
        { "type": "separator" },
        { "id": "save-layout", "label": "Enregistrer le layout", "shortcut": "Ctrl+S",
          "action": "layout:save" },
        { "id": "save-layout-as", "label": "Enregistrer sous…", "shortcut": "Ctrl+Shift+S",
          "action": "layout:save-as" },
        { "id": "load-layout", "label": "Charger un layout…", "action": "layout:load" },
        { "id": "reset-layout", "label": "Layout par défaut", "action": "layout:reset" },
        { "type": "separator" },
        { "id": "save-session", "label": "Enregistrer la session", "action": "session:save" },
        { "id": "load-session", "label": "Charger une session…", "action": "session:load" },
        { "type": "separator" },
        { "id": "manage-layouts", "label": "Gérer les layouts…", "action": "open:manage-layouts" },
        { "id": "manage-sessions", "label": "Gérer les sessions…", "action": "open:manage-sessions" }
      ]
    },
    {
      "id": "window",
      "label": "Fenêtre",
      "items": [
        {
          "id": "panels",
          "label": "Panneaux",
          "type": "panel-toggle"
        },
        { "type": "separator" },
        { "id": "reset-splits", "label": "Réinitialiser les proportions",
          "action": "layout:reset-splits" },
        { "id": "fullscreen", "label": "Plein écran", "shortcut": "F11",
          "action": "window:fullscreen" }
      ]
    },
    {
      "id": "theme",
      "label": "Thème",
      "items": [
        { "id": "theme-system", "label": "Système", "action": "theme:set", "value": "system" },
        { "id": "theme-light", "label": "Clair", "action": "theme:set", "value": "light" },
        { "id": "theme-dark", "label": "Sombre", "action": "theme:set", "value": "dark" },
        { "type": "separator" },
        { "id": "browse-themes", "label": "Choisir un thème…", "action": "theme:browse" }
      ]
    }
  ],

  "panelTree": {
    "direction": "horizontal",
    "sizes": [250, 100],
    "children": [
      {
        "direction": "vertical",
        "sizes": [60, 40],
        "children": [
          { "type": "panel", "id": "agents",
            "menu": [
              { "id": "launch", "label": "Lancer un agent", "action": "agent:launch" }
            ]
          },
          { "type": "panel", "id": "team-composition" }
        ]
      },
      {
        "type": "panel", "id": "chat",
        "visible": true,
        "closable": true
      }
    ]
  },

  "extensions": {
    "load": ["my-custom-panel"]
  }
}
```

### Règles du panelTree

- **Sans `type:`** = nœud splitter (`direction`, `sizes`, `children`)
- **`type: "panel"`** = feuille, référence un `panelId` du registre
- **`type: "ext"`** = extension, charge une URL externe
- **`visible`** = affiché/masqué (toggle depuis le menu Fenêtre > Panneaux)
- **`closable`** = l'utilisateur peut fermer ce panneau
- Les `sizes` sont en pourcentages ou pixels (`250px`)

### Menu dynamique

Le menu de chaque fenêtre est la **fusion** de :
1. Menu global du layout (Espace de travail, Fenêtre, Thème)
2. Les entrées `panel.menu` de chaque panneau visible dans le layout

---

## 3. Session — Multi-fenêtres

### Stockage

```
~/.modelweaver/sessions/
├── default.json
├── dev-full.json
└── debug.json
```

### Format

```json
{
  "id": "dev-full",
  "label": "Développement complet",
  "windows": [
    {
      "label": "dashboard",
      "title": "ModelWeaver — Dashboard",
      "width": 1400,
      "height": 900,
      "layout": "dashboard",
      "theme": "dark"
    },
    {
      "label": "sandbox",
      "title": "ModelWeaver — Agent Sandbox",
      "width": 1400,
      "height": 900,
      "layout": "sandbox",
      "theme": "dark"
    }
  ]
}
```

### Comportement

- Ouvrir une session = ouvrir N fenêtres Tauri avec leurs layouts
- Fermer une session = fermer toutes les fenêtres
- La session active est persistée dans `~/.modelweaver/active-session.json`
  (rétablie au prochain démarrage)

---

## 4. Thème

### Stockage

```
~/.modelweaver/themes/
├── dark.json
├── light.json
└── solarized.json
```

### Format

```json
{
  "id": "dark",
  "label": "Sombre",
  "colors": {
    "bg": "#1a1a2e",
    "bg-panel": "#16213e",
    "fg": "#e0e0e0",
    "accent": "#0f3460",
    "border": "#2a2a4a",
    "success": "#4ecca3",
    "warning": "#ffc857",
    "error": "#e84545"
  },
  "spacing": {
    "panel-padding": "12px",
    "border-radius": "6px"
  },
  "fonts": {
    "ui": "'Inter', system-ui, sans-serif",
    "mono": "'JetBrains Mono', monospace"
  }
}
```

Les variables sont injectées comme propriétés CSS personnalisées
(`--bg`, `--fg`, etc.) via un `<style>` dynamique au chargement de la fenêtre.

---

## 5. Panel — Composant atomique

Un panel est un composant React exporté d'un fichier dans `src/panels/`.

```typescript
// src/panels/MonPanel.tsx
export const MonPanel = {
  id: "mon-panel",           // référence dans le layout JSON
  label: "Mon Panneau",
  icon: "extension",         // Material Icon ou custom
  defaultSize: { width: 400, height: 300 },
  
  // Menu items injectés dans le menu de la fenêtre
  menu: [
    { id: "do-thing", label: "Faire un truc", action: "mon-panel:do-thing" }
  ],

  // Composant React
  component: () => { ... },
};
```

### Registre des panels

```typescript
// src/panels/index.ts
import { AgentsPanel } from './AgentsPanel';
import { ChatPanel } from './ChatPanel';
import { BundlesPanel } from './BundlesPanel';

export const PANEL_REGISTRY: Record<string, PanelDef> = {
  "agents": AgentsPanel,
  "chat": ChatPanel,
  "bundles": BundlesPanel,
  // …
};
```

Seule cette map doit être modifiée pour ajouter un panel natif (recompilation nécessaire).

---

## 6. Extension — Panel externe

### Structure

```
~/.modelweaver/extensions/my-panel/
├── manifest.json
└── dist/
    ├── index.html
    ├── index.js
    └── style.css
```

### manifest.json

```json
{
  "id": "my-panel",
  "name": "Mon Panneau",
  "version": "1.0.0",
  "icon": "extension",
  "entry": "dist/index.html",
  "permissions": ["daemon:read", "daemon:keys/list"]
}
```

### Fonctionnement

1. L'extension est servie par le daemon via `GET /extensions/my-panel/dist/index.html`
2. Le `PanelRenderer` crée un `<iframe>` pointant vers cette URL
3. L'extension communique avec le daemon via `fetch('/v1/...')` (même API que les panels natifs)
4. Un message `postMessage` permet des interactions basiques (redimensionnement, titre)

---

## 7. Routes daemon nécessaires

| Route | Description |
|-------|-------------|
| `layout/list` | Liste les layouts disponibles |
| `layout/get` | Contenu d'un layout |
| `layout/save` | Sauvegarde un layout |
| `layout/delete` | Supprime un layout |
| `session/list` | Liste les sessions |
| `session/get` | Contenu d'une session |
| `session/save` | Sauvegarde une session |
| `session/delete` | Supprime une session |
| `session/open` | Ouvre une session (multi-fenêtres) |
| `theme/list` | Liste les thèmes |
| `theme/get` | Contenu d'un thème |
| `extension/list` | Liste les extensions installées |
| `extension/{name}/*` | Sert les fichiers statiques |
| `panel/list` | Liste les panels natifs disponibles |

---

## 8. Implémentation — fichier par fichier

### Côté Rust (Tauri)

- `src-tauri/src/commands/layout.rs` — `open_window(layout_id)`, `open_session(session_id)`
- `src-tauri/src/commands/extension.rs` — `list_extensions()`

### Côté React

| Fichier | Rôle |
|---------|------|
| `src/panels/index.ts` | `PANEL_REGISTRY` — map de tous les panels natifs |
| `src/layout/PanelTreeRenderer.tsx` | Rendu récursif du `panelTree` JSON |
| `src/layout/MenuBar.tsx` | Menu généré depuis le JSON + panels visibles |
| `src/layout/ThemeEngine.tsx` | Injection des variables CSS du thème |
| `src/layout/useLayout.ts` | Hook : fetch layout, panels, menu, thème |
| `src/bridge.ts` | `daemonPost()` inchangé |
| `src/App.tsx` | Minimal : charge le layout → `PanelTreeRenderer` |

### Côté Python (daemon)

| Fichier | Ajout |
|---------|-------|
| `services/api/handlers/layouts.py` | Routes `layout/*`, `session/*`, `theme/*` |
| `services/api/handlers/extensions.py` | Route `extension/*` (fichiers statiques) |
| `services/api/handlers/panels.py` | Route `panel/list` (liste des panels natifs) |

---

## 9. Ordre d'implémentation

1. Routes daemon `layout/*`, `session/*`, `theme/*`, `panel/list`
2. Route daemon `extension/*` (fichiers statiques)
3. `ThemeEngine.tsx` — injection CSS depuis un thème JSON
4. `PanelTreeRenderer.tsx` — rendu récursif du panelTree
5. `useLayout.ts` — hook de chargement layout + thème
6. `MenuBar.tsx` — menu générique depuis le JSON
7. Migration des fenêtres existantes (dashboard, sandbox) vers le nouveau système
8. `layouts/default.json` — layout de base créé au premier lancement
9. Support des extensions (`type: "ext"` dans le panelTree)
10. Sessions multi-fenêtres (Rust : `WindowBuilder` depuis JSON)
