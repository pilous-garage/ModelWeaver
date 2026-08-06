// Résolution d'un layout → structure de rendu + menu global.
//
// Appelée après CHAQUE changement de layout. Produit :
//  - une structure de rendu (splits → groupes → occurrences)
//  - le menu global (items globaux + menus des panels présents + mini-layout actif)
//  - la liste des mini-layouts (pour le ciblage / prévisualisation)

import type { GroupNode, Layout, MenuItem, PanelOcc, SplitNode, TreeNode } from './types.ts';
import { listPresentPanels, paramsEqual } from './ops.ts';

export interface ResolvedGroup {
  kind: 'group';
  id: string;
  tabs: PanelOcc[];
  active: PanelOcc | null;
  hideTabs?: boolean;
}

export interface ResolvedSplit {
  kind: 'split';
  id: string;
  direction: 'horizontal' | 'vertical';
  sizes: number[];
  children: ResolvedNode[];
}

export type ResolvedNode = ResolvedSplit | ResolvedGroup;

export interface ResolvedLayout {
  root: ResolvedNode | null;
  menu: MenuItem[];
  /** tous les occIds présents (pour le ciblage inspecteur). */
  occIds: string[];
  /** les panels présents (id) avec leurs occIds. */
  panelOccurrences: { panel: string; occId: string; params?: Record<string, any> }[];
}

/** Données fenêtres/sessions injectées dans le menu « Affichage → Fenêtre ». */
export interface WindowMenuData {
  official: { id: string; title: string; layout?: string; theme?: string; width?: number; height?: number }[];
  registered: { window_id: string; title?: string }[];
  live: { window_id: string; title?: string }[];
  sessions: { id: string; name?: string; theme?: string | null; open_windows?: string[] }[];
  activeSession: { id: string; name?: string } | null;
  liveLabels: string[];
}

// ── Résolution d'un nœud ─────────────────────────────────────────────

function resolveNode(node: TreeNode | null, acc: { occIds: string[]; panelOccurrences: ResolvedLayout['panelOccurrences'] }): ResolvedNode | null {
  if (!node) return null;
  if (node.type === 'group') {
    const active = node.tabs.find((t) => t.occId === node.active) || node.tabs[0] || null;
    for (const t of node.tabs) {
      acc.occIds.push(t.occId);
      acc.panelOccurrences.push({ panel: t.panel, occId: t.occId, params: t.params });
      // mini-layout : résoudre le sous-arbre de l'onglet et collecter ses occIds
      if (t.tree) resolveNode(t.tree, acc);
    }
    return { kind: 'group', id: node.id, tabs: node.tabs, active, hideTabs: node.hideTabs };
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
 * Label d'un panel depuis son id (défaut : l'id). Utilisé par « Panneaux ouverts ».
 */
function panelLabelKey(panelId: string): string {
  return `panels.${panelId}.title`;
}

/**
 * Label d'un bundle (défaut : le bundle brut). Un bundle `__other` (panels sans
 * bundle déclaré) est traduit par « autres ».
 */
function bundleLabelKey(bundle: string): string {
  return bundle === '__other' ? 'bundle.autres' : `bundle.${bundle}`;
}

/**
 * Construit le menu global.
 * @param layout layout courant
 * @param panelMenus menus déclarés par les panels (id → items)
 * @param activeMiniLayoutId mini-layout actif (dont le menu est injecté) ou null
 * @param cataloguePanels catalogue des panels (id/labelKey/bundles)
 * @param windowMenu données fenêtres/sessions pour la section « Fenêtre »
 */
export function buildMenu(
  layout: Layout,
  panelMenus: Record<string, MenuItem[]>,
  activeMiniLayoutId: string | null = null,
  cataloguePanels?: { id: string; labelKey: string; bundles?: string[] }[],
  windowMenu?: WindowMenuData,
): MenuItem[] {
  const base: MenuItem[] = [];
  const menuItems: MenuItem[] = [];

  // ── DÉMARRER (ex-Fichier) ───────────────────────────────────────────
  base.push({
    labelKey: 'menu.demarrer', items: [
      { labelKey: 'menu.nouvelleFenetre', action: 'window:new' },
      { type: 'separator' },
      { labelKey: 'menu.quitter', action: 'app:quit' },
    ],
  });

  // Menu AFFICHAGE : regroupe les options de "vue" (Thèmes, fenêtres, panneaux…).
  const affichage: MenuItem[] = [];

  // Thèmes
  affichage.push({ labelKey: 'menu.themes', action: 'theme:set' });
  affichage.push({ type: 'separator' });

  // ── Section FENÊTRE ────────────────────────────────────────────────
  if (windowMenu) {
    const { official, registered, live, sessions, activeSession, liveLabels } = windowMenu;
    const isOpen = (id: string) => liveLabels.includes(id);

    // Sous-menu « Ouvrir une fenêtre » : officielles d'abord, puis enregistrées, puis vivantes.
    const openItems: MenuItem[] = [];
    if (official.length) {
      openItems.push({ labelKey: 'menu.fenetresOfficielles', items: official.map((o) => ({
        labelKey: o.title || o.id,
        suffix: isOpen(o.id) ? '→' : '+',
        action: `window:focus-or-open:${o.id}`,
      })) });
    }
    if (registered.length) {
      openItems.push({ labelKey: 'menu.fenetresEnregistrees', items: registered.map((r) => ({
        labelKey: r.title || r.window_id,
        suffix: isOpen(r.window_id) ? '→' : '+',
        action: `window:focus-or-open:${r.window_id}`,
      })) });
    }
    const liveNotListed = live.filter((w) => !isOpen(w.window_id));
    if (liveNotListed.length) {
      openItems.push({ labelKey: 'menu.fenetresVivantes', items: liveNotListed.map((w) => ({
        labelKey: w.title || w.window_id,
        suffix: '+',
        action: `window:focus-or-open:${w.window_id}`,
      })) });
    }
    affichage.push({ labelKey: 'menu.ouvrirFenetre', items: openItems });
    affichage.push({ labelKey: 'menu.nouvelleFenetreVierge', action: 'window:new-blank' });
    affichage.push({ labelKey: 'menu.enregistrerFenetre', action: 'window:register' });
    affichage.push({ labelKey: 'menu.resetLayoutFenetre', action: 'window:reset-layout' });
    affichage.push({ type: 'separator' });

    // ── Sessions ─────────────────────────────────────────────────────
    const sessionItems: MenuItem[] = sessions.map((s) => {
      const isActive = activeSession?.id === s.id;
      return {
        labelKey: s.name || s.id,
        suffix: isActive ? '●' : (s.open_windows?.length ? `(${s.open_windows.length})` : undefined),
        items: [
          // Ouvrir : désactivé si déjà active ; Fermer : désactivé si inactive.
          { labelKey: 'menu.sessionOuvrir', action: `session:open:${s.id}`, disabled: isActive },
          { labelKey: 'menu.sessionRenommer', action: `session:rename:${s.id}` },
          { labelKey: 'menu.sessionFermer', action: `session:close:${s.id}`, disabled: !isActive },
          { labelKey: 'menu.sessionSupprimer', action: `session:delete:${s.id}`, disabled: isActive },
        ],
      };
    });
    affichage.push({
      labelKey: 'menu.sessions', items: [
        { labelKey: 'menu.sessionNouvelle', action: 'session:new' },
        ...sessionItems,
      ],
    });
    affichage.push({ type: 'separator' });
  }

  // Map id → labelKey (catalogue) pour afficher les vrais labels des panels.
  const catalogLabel = new Map<string, string>();
  if (cataloguePanels) for (const p of cataloguePanels) catalogLabel.set(p.id, p.labelKey || p.id);

  // 1) Panneaux OUVERTS : liste des occurrences actives → clic = activer l'onglet.
  const opens = (() => {
    const out: MenuItem[] = [];
    (function walk(tree: TreeNode) {
      if (tree.type === 'group') {
        for (const t of tree.tabs) {
          out.push({
            labelKey: catalogLabel.get(t.panel) ?? panelLabelKey(t.panel),
            action: `panel:activate:${t.occId}`,
          });
          if (t.tree) walk(t.tree);
        }
      } else if (tree.type === 'split') {
        for (const c of tree.children) walk(c);
      }
    })(layout.tree);
    return out;
  })();
  affichage.push(opens.length > 0
    ? { labelKey: 'menu.panneauxOuverts', items: opens }
    : { labelKey: 'menu.panneauxOuverts', disabled: true, items: [] });
  affichage.push({ type: 'separator' });

  // 2) Ouvrir un nouveau panneau : trié par BUNDLES (un panel peut être dans
  //    plusieurs bundles). Panels sans bundle → bundle "Autres".
  if (cataloguePanels && cataloguePanels.length > 0) {
    const present = listPresentPanels(layout.tree);
    const catalog = cataloguePanels
      .sort((a, b) => (a.labelKey || a.id).localeCompare(b.labelKey || b.id));
    const bundles = new Map<string, { id: string; labelKey: string }[]>();
    for (const p of catalog) {
      const bs = (p.bundles && p.bundles.length > 0 ? p.bundles : ['__other']);
      for (const b of bs) {
        if (!bundles.has(b)) bundles.set(b, []);
        bundles.get(b)!.push(p);
      }
    }
    const bundleOrder = [...bundles.keys()].sort((a, b) => (a === '__other' ? 1 : b === '__other' ? -1 : a.localeCompare(b)));
    const newPanelItems: MenuItem[] = bundleOrder.map((b) => {
      const items = bundles.get(b)!.map((p) => {
        const has = present.some((occ) => occ.panel === p.id && paramsEqual(occ.params, undefined));
        return {
          labelKey: p.labelKey || p.id,
          // '+' = non présent (l'ajouter) ; '->' = présent (activer l'onglet).
          suffix: has ? '→' : '+',
          action: `panel:toggle:${p.id}`,
        };
      });
      return { labelKey: bundleLabelKey(b), items };
    });
    affichage.push({ labelKey: 'menu.ouvrirNouveau', items: newPanelItems });
    affichage.push({ type: 'separator' });
  }

  // 3) Mini-layout
  affichage.push({ labelKey: 'menu.miniLayout', action: 'mini-layout:add' });
  affichage.push({ type: 'separator' });

  // ── Plein écran (fenêtre / session) ────────────────────────────────
  affichage.push({
    labelKey: 'menu.pleinEcran', items: [
      { labelKey: 'menu.pleinEcranFenetre', action: 'window:fullscreen', shortcut: 'F11' },
      { labelKey: 'menu.pleinEcranSession', action: 'window:fullscreen-session' },
      { type: 'separator' },
      { labelKey: 'menu.quitterPleinEcran', action: 'window:fullscreen-exit', shortcut: 'F11' },
    ],
  });

  // ── Menus injectés des panels (fusionnés par 1er segment de path,
  //    avec une SECTION par panel) ────────────────────────────────────
  const panelSection: MenuItem[] = (() => {
    // Récupère les items de chaque panel PRÉSENT dans l'arbre (un seul passage).
    const present: { panelId: string; items: MenuItem[] }[] = [];
    const seen = new Set<string>();
    const walk = (node: TreeNode | null) => {
      if (!node) return;
      if (node.type === 'group') {
        for (const t of node.tabs) {
          const items = panelMenus[t.panel];
          if (items && items.length && !seen.has(t.panel)) {
            seen.add(t.panel);
            present.push({ panelId: t.panel, items });
          }
          if (t.tree) walk(t.tree);
        }
      } else {
        for (const c of node.children) walk(c);
      }
    };
    walk(layout.tree);

    // Fusion par 1er segment du path (ex. 'Panneaux'), section par panel.
    const byRoot = new Map<string, MenuItem[]>();
    for (const p of present) {
      const root = p.items[0]?.path?.[0] ?? 'menu.panneaux';
      if (!byRoot.has(root)) byRoot.set(root, []);
      // entête du panel (origine)
      byRoot.get(root)!.push({
        labelKey: `panels.${p.panelId}.titre`,
        disabled: true,
        style: 'section-header' as any,
      });
      // items du panel (sans le 1er segment de path redondant)
      for (const it of p.items) {
        const { path: _p, ...rest } = it;
        byRoot.get(root)!.push(rest);
      }
      byRoot.get(root)!.push({ type: 'separator' });
    }
    const out: MenuItem[] = [];
    for (const [root, items] of byRoot) {
      // retire le séparateur final
      if (items[items.length - 1]?.type === 'separator') items.pop();
      out.push({ labelKey: root, items });
    }
    return out;
  })();
  if (panelSection.length) {
    affichage.push(...panelSection);
    affichage.push({ type: 'separator' });
  }

  // ── Refresh (manuel) : le sous-menu est rempli côté App si des panels
  //    sont enregistrés (refreshCount > 0) ; sinon vide/hidden. ─────────
  affichage.push({ labelKey: 'menu.refresh', action: 'menu:refresh-placeholder' });

  base.push({ labelKey: 'menu.affichage', items: affichage });

  // menu_extra du layout
  menuItems.push(...(layout.menuExtra ?? []));

  // ── CONFIGURATION (Langue) ─────────────────────────────────────────
  base.push({
    labelKey: 'menu.configuration', items: [
      { labelKey: 'menu.langue', items: [{ labelKey: 'menu.langueFr', action: 'lang:set:fr' }, { labelKey: 'menu.langueEn', action: 'lang:set:en' }] },
    ],
  });

  // ── AIDE ───────────────────────────────────────────────────────────
  base.push({
    labelKey: 'menu.aide', items: [
      { labelKey: 'menu.aPropos', action: 'help:about' },
    ],
  });

  return [...base, ...menuItems];
}

// ── API principale ───────────────────────────────────────────────────

/** Résout un layout complet (structure + menu). */
export function resolveLayout(
  layout: Layout,
  panelMenus?: Record<string, MenuItem[]>,
  activeMiniLayoutId?: string | null,
  cataloguePanels?: { id: string; labelKey: string }[],
  windowMenu?: WindowMenuData,
): ResolvedLayout {
  const acc = { occIds: [] as string[], panelOccurrences: [] as ResolvedLayout['panelOccurrences'] };
  const root = resolveNode(layout.tree, acc);
  const menu = buildMenu(layout, panelMenus ?? {}, activeMiniLayoutId ?? null, cataloguePanels, windowMenu);
  return { root, menu, occIds: acc.occIds, panelOccurrences: acc.panelOccurrences };
}

/** Retourne les groupes visibles (pour le rendu des barres d'onglets). */
export function listGroups(node: TreeNode | null): ResolvedGroup[] {
  const out: ResolvedGroup[] = [];
  const walk = (n: TreeNode | null) => {
    if (!n) return;
    if (n.type === 'group') {
      out.push({ kind: 'group', id: n.id, tabs: n.tabs, active: n.tabs.find((t) => t.occId === n.active) || null, hideTabs: n.hideTabs });
      for (const t of n.tabs) {
        if (t.tree) walk(t.tree);
      }
    } else if (n.type === 'split') {
      for (const c of n.children) walk(c);
    }
  };
  walk(node);
  return out;
}
