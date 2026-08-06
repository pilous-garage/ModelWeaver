// Modèle de données de la GUI v2.
//
// Le layout est la source de vérité : toute modification de fenêtre passe par
// la mutation du layout, persisté en temps réel (.layout.yaml).
//
// Règle fondamentale : un panel vit TOUJOURS dans un onglet (group.tabs[]).
// Un groupe a au moins 1 onglet. Une slip view a un sous-arbre non vide.

// ── Occurrence de panel ──────────────────────────────────────────────

/**
 * Zoom d'un niveau (global, mini-layout ou panel). Stocké en float (×) :
 * 1 = 100%, 1.5 = 150%, 0.66 = 66%. `locked` fige l'effectif affiché (les
 * changements d'ancêtres compensent alors ce niveau pour garder la valeur).
 */
export interface ZoomState {
  value: number;
  locked?: boolean;
}

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
  /** titre PERSONNALISÉ de l'onglet (éditable par double-clic), sinon le label du panel. */
  label?: string;
  /** zoom propre de CET onglet (panel OU mini-layout). Défaut : 1× non locké. */
  zoom?: ZoomState;
  /**
   * MINI-LAYOUT : si défini, cet onglet rend ce sous-arbre (splits + groupes)
   * au lieu d'un composant. Un mini-layout est donc un PANEAU comme les autres
   * avec un layout interne ; on en a plusieurs par groupe (onglets), et le
   * drag/drop/reorder/cross-group/split fonctionnent nativement.
   */
  tree?: TreeNode;
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

/** Nœud split (direction + sizes + children). */
export interface SplitNode {
  type: 'split';
  /** id (généré à la création, utilisé pour le resize ciblé). */
  id?: string;
  direction: 'horizontal' | 'vertical';
  sizes?: number[];
  children: TreeNode[];
}

export type TreeNode = SplitNode | GroupNode;

// ── Layout ───────────────────────────────────────────────────────────

export interface Layout {
  id: string;
  label?: string;
  theme?: { global?: string; panel?: string };
  menuExtra?: MenuItem[];
  tree: TreeNode;
  /** zoom GLOBAL de la fenêtre (barre droite du menu). Défaut : 1× non locké. */
  zoom?: ZoomState;
}

// ── Menu ─────────────────────────────────────────────────────────────

export interface MenuItem {
  id?: string;
  labelKey?: string;
  type?: 'normal' | 'separator' | 'toggle' | 'radio';
  checked?: boolean;
  /** suffixe affiché à droite du label (ex. '+' pour ajouter, '→' pour activer). */
  suffix?: string;
  /** style spécial (ex. 'section-header' : entête de section de panel, non cliquable). */
  style?: 'section-header';
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
