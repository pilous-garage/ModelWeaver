# Contrat d'un fichier panneau

Tout panneau est un fichier unique dans `src/panels/<id>.tsx`.
Il **doit** exporter un objet `PanelDef` respectant le contrat ci-dessous.

---

## 1. Structure obligatoire

```typescript
// src/panels/mon-panel.tsx
import React from 'react';

export const MonPanel: PanelDef = {
  // ── Identification ─────────────────────────────────────
  id: "mon-panel",
  label: "Mon Panneau",
  icon: "extension",            // Material icon name
  version: "1.0.0",

  // ── Description ────────────────────────────────────────
  description: "Affiche et contrôle les machins-trucs.",
  descriptionLong: `Panneau principal pour la gestion des machins.
  Permet de lister, créer, modifier et supprimer des machins.
  Nécessite les routes daemon machine/list, machine/create.`,

  // ── Routes daemon nécessaires ───────────────────────────
  daemonRoutes: [
    { route: "machine/list",      methods: ["GET"],  desc: "Liste les machins" },
    { route: "machine/get",       methods: ["GET"],  desc: "Détail d'un machin" },
    { route: "machine/create",    methods: ["POST"], desc: "Crée un machin" },
    { route: "machine/delete",    methods: ["POST"], desc: "Supprime un machin" },
  ],

  // ── Menu inséré dans la fenêtre ─────────────────────────
  // Chaque entrée est insérée dans le menu de la fenêtre
  // à l'emplacement correspondant à son `menuPath`.
  menu: [
    {
      menuPath: ["Fichier"],            // sous "Fichier > "
      id: "mon-panel:new",
      label: "Nouveau machin",
      shortcut: "Ctrl+Shift+M",
      action: "panel:mon-panel:new",
    },
    {
      menuPath: ["Affichage", "Panneaux"],  // sous "Affichage > Panneaux"
      id: "mon-panel:toggle",
      label: "Mon Panneau",
      type: "toggle-visibility",
    },
  ],

  // ── Déclaration textuelle (debug/log/CLI) ──────────────
  declaration(): string {
    // Retourne une description lisible de ce que ce panneau expose.
    // Utilisée pour les logs, le debug, et l'affichage CLI.
    return JSON.stringify(this, null, 2);
  },

  // ── Cycle de vie ───────────────────────────────────────
  onActivate(ctx: PanelContext): void {
    // Appelé quand le panneau devient visible
    // Ex: démarrer un polling
  },
  onDeactivate(ctx: PanelContext): void {
    // Appelé quand le panneau devient masqué
    // Ex: arrêter le polling
  },
  onRefresh(ctx: PanelContext): Promise<void> {
    // Appelé par le menu "Rafraîchir" ou par le système
    // Ex: recharger les données depuis le daemon
  },

  // ── Rendu React ────────────────────────────────────────
  component: React.FC<PanelProps>,
};
```

---

## 2. Définition des types

```typescript
interface PanelDef {
  // ── Identification ───────────────────────────────────
  id: string;
  label: string;
  icon?: string;
  version: string;

  // ── Description ───────────────────────────────────────
  description: string;            // une ligne, pour les tooltips
  descriptionLong?: string;       // multi-ligne, pour la CLI

  // ── Routes daemon déclarées ────────────────────────────
  daemonRoutes: DaemonRouteDeclaration[];

  // ── Menu inséré ────────────────────────────────────────
  menu?: PanelMenuItemDef[];

  // ── Déclaration textuelle (debug/log/CLI) ──────────────
  declaration(): string;

  // ── Cycle de vie ───────────────────────────────────────
  onActivate?(ctx: PanelContext): void;
  onDeactivate?(ctx: PanelContext): void;
  onRefresh?(ctx: PanelContext): Promise<void>;

  // ── Rendu ──────────────────────────────────────────────
  defaultSize?: { width?: number; height?: number };
  component: React.FC<PanelProps>;
}

interface DaemonRouteDeclaration {
  route: string;                 // ex: "machine/list"
  methods: ("GET" | "POST" | "DELETE" | "PUT")[];
  desc: string;                  // description lisible
  params?: Record<string, string>; // paramètres attendus
}

interface PanelMenuItemDef {
  menuPath: string[];             // chemin dans le menu (ex: ["Fichier", "Nouveau"])
  id: string;                    // identifiant unique
  label: string;                 // texte affiché
  shortcut?: string;             // racourci clavier
  type?: "normal" | "toggle-visibility" | "separator";
  action: string;                // déclenché par le clic
  disabled?: boolean;            // optionnellement grisé
}

interface PanelContext {
  api: AppApi;                    // pour appeler le daemon
  layout: LayoutConfig;           // layout courant
  theme: ThemeConfig;             // thème courant
  onMenuAction: (action: string) => void;
}

interface PanelProps {
  ctx: PanelContext;
}
```

---

## 3. Déclaration textuelle — `declaration()`

Cette fonction est utilisée partout où le panneau doit rendre compte
de ce qu'il expose sans lancer React :

```typescript
declaration(): string {
  const lines = [
    `[${this.id}] ${this.label} v${this.version}`,
    `  Description : ${this.description}`,
    `  Routes daemon :`,
  ];
  for (const r of this.daemonRoutes) {
    const methods = r.methods.join("/");
    lines.push(`    ${methods} ${r.route} — ${r.desc}`);
  }
  if (this.menu?.length) {
    lines.push(`  Menu :`);
    for (const m of this.menu) {
      const path = [...m.menuPath, m.label].join(" > ");
      lines.push(`    ${m.action} → ${path}${m.shortcut ? ` (${m.shortcut})` : ""}`);
    }
  }
  return lines.join("\n");
}
```

Exemple de sortie :

```
[agents] Agents v1.0.0
  Description : Liste et contrôle des agents actifs
  Routes daemon :
    GET agent/list — Liste des agents
    GET agent/get — Détail d'un agent
    POST agent/signal — Pause/Resume/Kill
    GET agent/metrics — Métriques tokens/latence
  Menu :
    panel:agents:refresh → Affichage > Rafraîchir
    panel:agents:launch → Fichier > Lancer un agent…
    panel:agents:pause → Agent > Mettre en pause (quand actif)
```

---

## 4. Menu — `menu`

Chaque entrée de menu déclare un `menuPath` qui détermine son emplacement
dans l'arbre du menu de la fenêtre.

```typescript
menu: [
  // Sous "Fichier > Nouveau > Machin"
  { menuPath: ["Fichier", "Nouveau"], label: "Machin", action: "panel:mon-panel:new" },

  // Entrée directe sous le nom du panneau (visible si plusieurs actions)
  { menuPath: [], label: "Faire un truc", action: "panel:mon-panel:do-thing" },
]
```

Les entrées avec `menuPath: []` sont regroupées automatiquement
sous une section au nom du panneau dans le menu.

**Fusion** : au runtime, les entrées de tous les panneaux **visibles**
sont fusionnées dans le menu de la fenêtre. Les entrées des panneaux
masqués sont retirées.

---

## 5. Cycle de vie

| Fonction | Quand |
|----------|-------|
| `onActivate(ctx)` | Panneau devient visible (ouvert ou démasqué) |
| `onDeactivate(ctx)` | Panneau devient invisible (fermé ou masqué) |
| `onRefresh(ctx)` | Appelé par le menu "Rafraîchir" ou timer interne |

Le panneau peut lancer des timers/polling dans `onActivate` et les
nettoyer dans `onDeactivate`. Exemple :

```typescript
onActivate(ctx) { this._timer = setInterval(() => ctx.api.daemonPost(...), 5000); }
onDeactivate(ctx) { clearInterval(this._timer); }
```

---

## 6. Traduction / CLI

Chaque panneau peut optionnellement exposer une description
utilisable en ligne de commande :

```typescript
export const MonPanel: PanelDef = {
  // ...
  cliSummary: "gère les machins-trucs",
  cliCommands: [
    { cmd: "list", desc: "liste les machins" },
    { cmd: "create", desc: "crée un machin", args: ["--name"] },
  ],
};
```

Ces commandes CLI sont accessibles via `mw panel mon-panel list`.

---

## 7. Exemple complet minimal

```typescript
import React from 'react';

export const HelloPanel: PanelDef = {
  id: "hello",
  label: "Hello",
  version: "1.0.0",
  description: "Panneau d'exemple qui dit bonjour",
  daemonRoutes: [],
  menu: [],

  declaration() {
    return `[hello] Hello v1.0.0 — Panneau d'exemple`;
  },

  component: ({ ctx }) => React.createElement("div", null, "Hello !"),
};
```

---

## 8. Vérification statique

Un script de vérification (`scripts/check-panels.ts`) pourra :

1. Parcourir tous les fichiers dans `src/panels/`
2. Vérifier qu'ils exportent un `PanelDef` valide
3. Vérifier que toutes les routes déclarées existent dans le daemon
4. Vérifier qu'il n'y a pas de conflit de `id` entre panneaux
5. Afficher la `declaration()` de chaque panneau = inventaire complet
