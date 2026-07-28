# Spécification des panneaux GUI

Chaque panneau est un composant React autonome dans `src/panels/`.
Il est référencé dans un layout JSON par son `id` et peut insérer
des entrées de menu dynamiques dans la fenêtre qui l'affiche.

---

## 1. Contrat d'un panneau

```typescript
interface PanelDef {
  id: string;                  // identifiant unique, référencé dans les layouts
  label: string;               // libellé affiché dans l'onglet/le menu
  icon?: string;               // icône Material (optionnelle)

  defaultSize?: {
    width?: number;            // largeur par défaut en pixels
    height?: number;           // hauteur par défaut en pixels
  };

  props?: Record<string, any>; // props passées depuis le layout JSON

  // Entrées de menu injectées quand ce panneau est visible
  menu?: PanelMenuItem[];

  description?: string;        // texte d'aide (tooltip, palette)

  component: React.FC<PanelProps>;
}

interface PanelMenuItem {
  id: string;
  label: string;
  shortcut?: string;
  action: string;              // préfixée par "panel:{id}:{action}"
  icon?: string;
  disabled?: boolean;
  items?: PanelMenuItem[];      // sous-menu
}

interface PanelProps {
  api: AppApi;                 // hook useApp() partagé
  layout: LayoutConfig;        // layout courant
  theme: ThemeConfig;          // thème courant
  onMenuAction: (action: string) => void;
}
```

## 2. Registre

Tous les panneaux sont enregistrés dans `src/panels/index.ts` :

```typescript
export const PANEL_REGISTRY: Record<string, PanelDef> = {
  "agents": AgentsPanel,
  "chat": ChatPanel,
  "bundles": BundlesPanel,
  // …
};
```

Pour ajouter un panneau natif :
1. Créer `src/panels/MonPanel.tsx` respectant le contrat `PanelDef`
2. L'importer dans `src/panels/index.ts` et l'ajouter à `PANEL_REGISTRY`
3. Recompiler le GUI

---

## 3. Liste des panneaux

### 3.1 AgentsPanel

| Champ | Valeur |
|-------|--------|
| **id** | `agents` |
| **label** | Agents |
| **icon** | `smart_toy` |
| **description** | Liste et contrôle des agents actifs |
| **defaultSize** | largeur: 300 |

**Routes daemon utilisées :**
| Route | Usage | Fréquence |
|-------|-------|-----------|
| `agent/list` | Liste des agents | au montage + refresh 5s |
| `agent/get` | Détail d'un agent | au clic |
| `agent/signal` | Pause/Resume/Kill | au clic bouton |
| `agent/metrics` | Métriques tokens/latence | refresh 5s |

**Menu inséré :**
| Label | Action | Condition |
|-------|--------|-----------|
| Rafraîchir | `panel:agents:refresh` | toujours |
| Lancer un agent… | `panel:agents:launch` | toujours |
| — | | |
| Mettre en pause | `panel:agents:pause` | agent sélectionné actif |
| Reprendre | `panel:agents:resume` | agent sélectionné en pause |
| Arrêter | `panel:agents:stop` | agent sélectionné actif |
| — | | |
| Voir les métriques | `panel:agents:metrics` | agent sélectionné |

---

### 3.2 ChatPanel

| Champ | Valeur |
|-------|--------|
| **id** | `chat` |
| **label** | Chat |
| **icon** | `chat` |
| **description** | Session de chat avec un LLM |
| **defaultSize** | largeur: 400 |

**Routes daemon utilisées :**
| Route | Usage |
|-------|-------|
| `chat/session/list` | Liste des sessions |
| `chat/session/get` | Historique d'une session |
| `chat/session/send` | Envoyer un message |
| `chat/session/delete` | Supprimer une session |

**Menu inséré :**
| Label | Action |
|-------|--------|
| Nouvelle conversation | `panel:chat:new` |
| Effacer la conversation | `panel:chat:clear` |

---

### 3.3 BundlesPanel

| Champ | Valeur |
|-------|--------|
| **id** | `bundles` |
| **label** | Bundles |
| **icon** | `inventory_2` |
| **description** | Gestion des bundles de skills |
| **defaultSize** | largeur: 350 |

**Routes daemon utilisées :**
| Route | Usage |
|-------|-------|
| `catalogue/bundles/list` | Liste des bundles |
| `catalogue/bundles/get` | Contenu YAML d'un bundle |
| `catalogue/bundles/save` | Sauvegarder un bundle |

**Menu inséré :**
| Label | Action |
|-------|--------|
| Nouveau bundle | `panel:bundles:new` |
| Enregistrer | `panel:bundles:save` |

---

### 3.4 ToolsPanel

| Champ | Valeur |
|-------|--------|
| **id** | `tools` |
| **label** | Outils |
| **icon** | `build` |
| **description** | Outils Registry YAML (delegate, edit, grep) |
| **defaultSize** | largeur: 300 |

**Routes daemon utilisées :**
| Route | Usage |
|-------|-------|
| `catalogue/tools/list` | Liste des outils |
| `catalogue/tools/get` | Détail d'un outil |

**Menu inséré :** aucun.

---

### 3.5 TeamCompositionPanel

| Champ | Valeur |
|-------|--------|
| **id** | `team-composition` |
| **label** | Équipe |
| **icon** | `groups` |
| **description** | Composition et gestion des équipes |
| **defaultSize** | largeur: 300 |

**Routes daemon utilisées :**
| Route | Usage |
|-------|-------|
| `team/list` | Liste des équipes |
| `team/get` | Détail d'une équipe |
| `team/add-member` | Ajouter un membre |
| `team/init-workspace` | Initialiser un workspace |
| `agent/list-by-team` | Membres par équipe |

**Menu inséré :**
| Label | Action |
|-------|--------|
| Nouvelle équipe… | `panel:team:new` |
| Initialiser le workspace | `panel:team:init-ws` |

---

### 3.6 AgentMonitoringPanel

| Champ | Valeur |
|-------|--------|
| **id** | `monitoring` |
| **label** | Monitoring |
| **icon** | `monitoring` |
| **description** | Métriques temps réel des agents |
| **defaultSize** | hauteur: 250 |

**Routes daemon utilisées :**
| Route | Usage |
|-------|-------|
| `agent/metrics` | Métriques globales |
| `agent/stream` | Flux temps réel |

**Menu inséré :**
| Label | Action |
|-------|--------|
| Pause / Reprendre | `panel:monitoring:live-toggle` |

---

### 3.7 CataloguePanel

| Champ | Valeur |
|-------|--------|
| **id** | `catalogue` |
| **label** | Catalogue |
| **icon** | `database` |
| **description** | Catalogue des modèles LLM |
| **defaultSize** | largeur: 300 |

**Routes daemon utilisées :**
| Route | Usage |
|-------|-------|
| `llm/models/list` | Modèles par provider |
| `llm/recommend` | Recommandation LLM |
| `providers/list` | Liste des providers |

**Menu inséré :** aucun.

---

### 3.8 KeysPanel

| Champ | Valeur |
|-------|--------|
| **id** | `keys` |
| **label** | Clés API |
| **icon** | `key` |
| **description** | Gestion des clés API |
| **defaultSize** | largeur: 300 |

**Routes daemon utilisées :**
| Route | Usage |
|-------|-------|
| `keys/list` | Liste des clés |
| `keys/set` | Ajouter/modifier une clé |
| `keys/delete` | Supprimer une clé |
| `keys/set_lock` | Verrouiller/déverrouiller |

**Menu inséré :**
| Label | Action |
|-------|--------|
| Ajouter une clé… | `panel:keys:add` |

---

### 3.9 LocalModelsPanel

| Champ | Valeur |
|-------|--------|
| **id** | `local-models` |
| **label** | LLM locaux |
| **icon** | `computer` |
| **description** | Moteurs LLM locaux (Ollama, LM Studio) |
| **defaultSize** | largeur: 300 |

**Routes daemon utilisées :**
| Route | Usage |
|-------|-------|
| `llm/local/list` | Moteurs détectés |
| `llm/local/start` | Démarrer un moteur |
| `llm/local/stop` | Arrêter un moteur |

**Menu inséré :**
| Label | Action |
|-------|--------|
| Démarrer tout | `panel:local-models:start-all` |

---

### 3.10 InstalledToolsPanel

| Champ | Valeur |
|-------|--------|
| **id** | `installed-tools` |
| **label** | Outils installés |
| **icon** | `checklist` |
| **description** | Outils système (opencode, litellm…) |
| **defaultSize** | largeur: 300 |

**Routes daemon utilisées :**
| Route | Usage |
|-------|-------|
| `tools/installed/list` | Liste des outils |
| `tools/install` | Installer un outil |

**Menu inséré :** aucun.

---

### 3.11 DebugPanel

| Champ | Valeur |
|-------|--------|
| **id** | `debug` |
| **label** | Debug |
| **icon** | `bug_report` |
| **description** | Logs, processus, ressources |
| **defaultSize** | largeur: 400, hauteur: 200 |

**Routes daemon utilisées :**
| Route | Usage |
|-------|-------|
| `logs/read` | Logs applicatifs |
| `service/list` | Services actifs |
| `system/state/get` | État du système |

**Menu inséré :**
| Label | Action |
|-------|--------|
| Vider les logs | `panel:debug:clear-logs` |

---

### 3.12 DashboardPanel (fenêtre dashboard)

| Champ | Valeur |
|-------|--------|
| **id** | `dashboard` |
| **label** | Dashboard |
| **icon** | `dashboard` |
| **description** | Panneau principal de la fenêtre dashboard |
| **defaultSize** | plein écran |

**Routes daemon utilisées :** fusion des routes des sous-panneaux.

**Menu inséré :** aucun (c'est le panneau racine).

---

### 3.13 À créer — Panneaux manquants

| Panel | id | Routes nécessaires | Statut |
|-------|----|--------------------|--------|
| **Permissions** | `permissions` | — (éditeur inline dans l'agent) | à faire |
| **Workspace** | `workspace` | `team/init-workspace` | à faire |
| **Sessions** | `sessions` | `session/list/get/save` | attend layout system |
| **Layouts** | `layouts` | `layout/list/get/save` | attend layout system |
| **Thèmes** | `themes` | `theme/list/get` | attend layout system |
| **Extensions** | `extensions` | `extension/list` | attend layout system |
| **Agent Sandbox IDE** | `sandbox` | `catalogue/agents/*` + bundles/tools | existe déjà |

---

## 4. Migration des panels existants

Chaque panel dans `src/panels/*.tsx` doit être migré vers le format `PanelDef` :

```typescript
// Avant (actuel)
export function AgentsPanel({ api }: { api: AppApi }) { ... }

// Après (format PanelDef)
export const AgentsPanel: PanelDef = {
  id: "agents",
  label: "Agents",
  icon: "smart_toy",
  menu: [ ... ],
  component: ({ api }) => { ... },
};
```

La migration peut être faite panel par panel, sans tout casser.
Les layouts JSON existants continueront de fonctionner si le `id` ne change pas.
