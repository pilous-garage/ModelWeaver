// Résolution d'un layout → structure de rendu + menu global.
//
// Appelée après CHAQUE changement de layout. Produit :
//  - une structure de rendu (splits → groupes → occurrences)
//  - le menu global (items globaux + menus des panels présents + mini-layout actif)
//  - la liste des mini-layouts (pour le ciblage / prévisualisation)

import type { GroupNode, Layout, MenuItem, PanelOcc, MiniLayoutNode, SplitNode, TreeNode } from './types.ts';
import { listPresentPanels, paramsEqual } from './ops.ts';

export interface ResolvedGroup {
  kind: 'group';
  id: string;
  tabs: PanelOcc[];
  active: PanelOcc | null;
  hideTabs?: boolean;
}

export interface ResolvedMiniLayout {
  kind: "miniLayout";
  id: string;
  title?: string;
  children: ResolvedNode[];
  menuExtra: MenuItem[];
}

export interface ResolvedSplit {
  kind: 'split';
  id: string;
  direction: 'horizontal' | 'vertical';
  sizes: number[];
  children: ResolvedNode[];
}

export type ResolvedNode = ResolvedSplit | ResolvedGroup | ResolvedMiniLayout;

export interface ResolvedLayout {
  root: ResolvedNode | null;
  menu: MenuItem[];
  MiniLayouts: MiniLayoutNode[];
  /** tous les occIds présents (pour le ciblage inspecteur). */
  occIds: string[];
  /** les panels présents (id) avec leurs occIds. */
  panelOccurrences: { panel: string; occId: string; params?: Record<string, any> }[];
}

// ── Résolution d'un nœud ─────────────────────────────────────────────

function resolveNode(node: TreeNode | null, acc: { occIds: string[]; panelOccurrences: ResolvedLayout['panelOccurrences'] }): ResolvedNode | null {
  if (!node) return null;
  if (node.type === 'group') {
    const active = node.tabs.find((t) => t.occId === node.active) || node.tabs[0] || null;
    for (const t of node.tabs) {
      acc.occIds.push(t.occId);
      acc.panelOccurrences.push({ panel: t.panel, occId: t.occId, params: t.params });
    }
    return { kind: 'group', id: node.id, tabs: node.tabs, active, hideTabs: node.hideTabs };
  }
  if (node.type === "miniLayout") {
    const children = resolveNode(node.tree, acc);
    return {
      kind: "miniLayout", id: node.id, title: node.title,
      children: children ? [children] : [],
      menuExtra: node.menuExtra ?? [],
    };
  }
  // split
  const children = (node.children || [])
    .map((c) => resolveNode(c, acc))
    .filter((c): c is ResolvedNode => c !== null);
  const n = children.length;
  const sizes = (node.sizes && node.sizes.length === n)
    ? node.sizes
    : Array(n).fill(Math.round(100 / Math.max(n, 1)));
  return { kind: 'split', id: node.id ?? '', direction: node.direction, sizes, children };
}

// ── Menu ─────────────────────────────────────────────────────────────

/**
 * Construit le menu global.
 * @param layout layout courant
 * @param panelMenus menus déclarés par les panels (id → items)
 * @param activeMiniLayoutId mini-layout actif (dont le menu est injecté) ou null
 */
export function buildMenu(
  layout: Layout,
  panelMenus: Record<string, MenuItem[]>,
  activeMiniLayoutId: string | null = null,
  cataloguePanels?: { id: string; labelKey: string }[],
): MenuItem[] {
  const base: MenuItem[] = [];
  const menuItems: MenuItem[] = [];

  // Menu global fixe
  base.push({
    labelKey: 'menu.fichier', items: [
      { labelKey: 'menu.nouvelleFenetre', action: 'window:new' },
      { type: 'separator' },
      { labelKey: 'menu.quitter', action: 'app:quit' },
    ],
  });
  base.push({
    labelKey: 'menu.fenetre', items: [
      { labelKey: 'menu.ouvrir', action: 'window:open' },
      { labelKey: 'menu.enregistrer', action: 'layout:save' },
      { labelKey: 'menu.pleinEcran', action: 'window:fullscreen', shortcut: 'F11' },
    ],
  });
  base.push({ labelKey: 'menu.affichage', items: [{ labelKey: 'menu.themes', action: 'theme:set' }] });
  base.push({ labelKey: 'menu.langue', items: [{ labelKey: 'menu.langueFr', action: 'lang:set:fr' }, { labelKey: 'menu.langueEn', action: 'lang:set:en' }] });

  // Menu Panneaux → Catalogue (tous les panels, action panel:add:<id>)
  if (cataloguePanels && cataloguePanels.length > 0) {
    // Unicité par fenêtre : un panel déjà présent (id + params identiques,
    // tous sous-layouts confondus) est retiré du catalogue. L'item du
    // catalogue ajoute sans params → on compare contre undefined/{}.
    const present = listPresentPanels(layout.tree);
    const catalogue = cataloguePanels
      .filter((p) => !present.some((occ) => occ.panel === p.id && paramsEqual(occ.params, undefined)))
      .sort((a, b) => (a.labelKey || a.id).localeCompare(b.labelKey || b.id));
    base.push({
      labelKey: 'menu.panneaux', items: [
        {
          labelKey: 'menu.onglet', items: catalogue.map((p) => ({
            labelKey: p.labelKey || p.id,
            action: `panel:add:${p.id}`,
          })),
        },
        {
          labelKey: 'menu.miniLayout', items: catalogue.map((p) => ({
            labelKey: p.labelKey || p.id,
            action: `mini-layout:add:${p.id}`,
          })),
        },
      ],
    });
  }

  // menu_extra du layout
  menuItems.push(...(layout.menuExtra ?? []));

  // Menus des panels présents dans l'arbre
  const seen = new Set<string>();
  const walkForMenus = (node: TreeNode | null) => {
    if (!node) return;
    if (node.type === 'group') {
      for (const t of node.tabs) {
        const items = panelMenus[t.panel];
        if (items && !seen.has(t.panel)) {
          seen.add(t.panel);
          menuItems.push(...items);
        }
      }
    } else if (node.type === "miniLayout") {
      // le menu du mini-layout n'est injecté que si elle est active
      if (node.id === activeMiniLayoutId) menuItems.push(...(node.menuExtra ?? []));
      walkForMenus(node.tree);
    } else {
      for (const c of node.children) walkForMenus(c);
    }
  };
  walkForMenus(layout.tree);

  return [...base, ...menuItems];
}

// ── API principale ───────────────────────────────────────────────────

/** Résout un layout complet (structure + menu + MiniLayouts). */
export function resolveLayout(
  layout: Layout,
  panelMenus?: Record<string, MenuItem[]>,
  activeMiniLayoutId?: string | null,
  cataloguePanels?: { id: string; labelKey: string }[],
): ResolvedLayout {
  const acc = { occIds: [] as string[], panelOccurrences: [] as ResolvedLayout['panelOccurrences'] };
  const root = resolveNode(layout.tree, acc);
  const MiniLayouts: MiniLayoutNode[] = [];
  collectMiniLayouts(layout.tree, MiniLayouts);
  const menu = buildMenu(layout, panelMenus ?? {}, activeMiniLayoutId ?? null, cataloguePanels);
  return { root, menu, MiniLayouts, occIds: acc.occIds, panelOccurrences: acc.panelOccurrences };
}

function collectMiniLayouts(node: TreeNode | null, out: MiniLayoutNode[]) {
  if (!node) return;
  if (node.type === "miniLayout") {
    out.push(node);
    collectMiniLayouts(node.tree, out);
  } else if (node.type === 'split') {
    for (const c of node.children) collectMiniLayouts(c, out);
  } else if (node.type === 'group') {
    // pas de MiniLayouts dans un groupe (les MiniLayouts sont des nœuds)
  }
}

/** Retourne les groupes visibles (pour le rendu des barres d'onglets). */
export function listGroups(node: TreeNode | null): ResolvedGroup[] {
  const out: ResolvedGroup[] = [];
  const walk = (n: TreeNode | null) => {
    if (!n) return;
    if (n.type === 'group') {
      out.push({ kind: 'group', id: n.id, tabs: n.tabs, active: n.tabs.find((t) => t.occId === n.active) || null, hideTabs: n.hideTabs });
    } else if (n.type === 'split') {
      for (const c of n.children) walk(c);
    } else {
      walk(n.tree);
    }
  };
  walk(node);
  return out;
}
