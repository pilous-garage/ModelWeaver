// Modèle de données de la GUI v2.
//
// Le layout est la source de vérité : toute modification de fenêtre passe par
// la mutation du layout, persisté en temps réel (.layout.yaml).
//
// Règle fondamentale : un panel vit TOUJOURS dans un onglet (group.tabs[]).
// Un groupe a au moins 1 onglet. Une slip view a un sous-arbre non vide.

// ── Occurrence de panel ──────────────────────────────────────────────

/** Occurrence d'un panel dans un onglet. */
export interface PanelOcc {
  /** id du panel (registry). */
  panel: string;
  /** id d'occurrence unique dans l'arbre (auto-généré si absent). */
  occId: string;
  /** arguments passés au composant (facultatif). */
  params?: Record<string, any>;
  /** thème panel surchargé pour CETTE occurrence (facultatif). */
  theme?: { panel?: string };
}

// ── Nœuds de l'arbre ─────────────────────────────────────────────────

/** Groupe d'onglets. */
export interface GroupNode {
  type: 'group';
  id: string;
  tabs: PanelOcc[];
  active: string; // occId de l'onglet actif
  /** cache la barre d'onglets (mode "panel pleine espace") — futur. */
  hideTabs?: boolean;
}

/**
 * Mini-layout : un LAYOUT IMBRIQUÉ dans un nœud, avec son propre onglet.
 * C'est le concept "fake-window" : une fenêtre interne avec son propre
 * sous-arbre (splits + groupes), un titre, un onglet pour la switcher, et ses
 * propres menus. Permet de basculer rapidement d'un mini-layout à l'autre.
 * (Ancien nom : "slip view" — renommé en mini-layout.)
 */
export interface MiniLayoutNode {
  type: 'miniLayout';
  id: string;
  title?: string;
  tree: TreeNode; // sous-arbre (non vide)
  menuExtra?: MenuItem[];
}

/** Nœud split (direction + sizes + children). */
export interface SplitNode {
  type: 'split';
  /** id (généré à la création, utilisé pour le resize ciblé). */
  id?: string;
  direction: 'horizontal' | 'vertical';
  sizes?: number[];
  children: TreeNode[];
}

export type TreeNode = SplitNode | GroupNode | MiniLayoutNode;

// ── Layout ───────────────────────────────────────────────────────────

export interface Layout {
  id: string;
  label?: string;
  theme?: { global?: string; panel?: string };
  menuExtra?: MenuItem[];
  tree: SplitNode | GroupNode | MiniLayoutNode;
}

// ── Menu ─────────────────────────────────────────────────────────────

export interface MenuItem {
  id?: string;
  labelKey?: string;
  type?: 'normal' | 'separator' | 'toggle' | 'radio';
  checked?: boolean;
  action?: string;
  shortcut?: string;
  disabled?: boolean;
  items?: MenuItem[];
  /** chemin de sous-menus pour la fusion (ex. ["Panneaux", "Ressources"]). */
  path?: string[];
}

// ── Session ──────────────────────────────────────────────────────────

export interface SessionWindow {
  id: string;
  title?: string;
  layout: string; // référence un .layout.yaml
  theme?: { global?: string; panel?: string };
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  maximized?: boolean;
}

export interface Session {
  id: string;
  label?: string;
  theme?: { global?: string; panel?: string };
  windows: SessionWindow[];
}

// ── Thème ────────────────────────────────────────────────────────────

export interface ThemeRef {
  /** nom du thème global (interface : menu, onglets, splits). */
  global?: string;
  /** nom du thème panel (contenu des panels). */
  panel?: string;
}

// ── Helpers d'identité ───────────────────────────────────────────────

let _idCounter = 0;

/** Génère un id unique (occId, groupId, etc.). */
export function genId(prefix = 'id'): string {
  _idCounter += 1;
  return `${prefix}-${Date.now().toString(36)}-${_idCounter}`;
}
