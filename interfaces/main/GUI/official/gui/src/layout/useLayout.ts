/** useLayout — Hook de chargement du layout, thème, menu */

import { useState, useEffect, useCallback, useRef } from 'react';
import { daemonPost } from '../bridge.ts';
import { PANEL_REGISTRY } from '../panels/index.ts';
import { logGui } from '../gui_log.ts';

export interface LayoutConfig {
  id: string;
  label?: string;
  theme?: string;
  menu?: MenuItemDef[];
  panelTree?: PanelTreeNode;
}

export interface PanelTreeNode {
  direction?: 'horizontal' | 'vertical';
  sizes?: number[];
  children?: PanelTreeNode[];
  type?: 'panel' | 'group' | 'ext';
  id?: string;
  url?: string;
  visible?: boolean;
  closable?: boolean;
  /** Groupe d'onglets (ancien système) : tabs + activeTab. Un nœud `panel`
   * simple est rétro-compatible (groupe à 1 onglet). */
  tabs?: string[];
  activeTab?: string;
  groupId?: string;
}

export interface MenuItemDef {
  id?: string;
  label?: string;
  type?: 'normal' | 'separator' | 'toggle-visibility';
  shortcut?: string;
  action?: string;
  items?: MenuItemDef[];
  disabled?: boolean;
  value?: string;
  source?: string;
}

export interface ThemeConfig {
  id: string;
  label?: string;
  colors?: Record<string, string>;
  spacing?: Record<string, string>;
  fonts?: Record<string, string>;
}

const FALLBACK_LAYOUTS: Record<string, LayoutConfig> = {
  'default': {
    id: 'default', label: 'default', theme: 'dark', menu: [],
    panelTree: {
      direction: 'horizontal',
      sizes: [33, 34, 33],
      children: [
        { type: 'panel', id: 'systeme-dashboard', visible: true, closable: false },
        { type: 'panel', id: 'communication-chat', visible: true, closable: false },
        { type: 'panel', id: 'agents-liste', visible: true, closable: false },
      ],
    },
  },
  'dashboard': {
    id: 'dashboard', label: 'dashboard', theme: 'dark', menu: [],
    panelTree: {
      direction: 'horizontal',
      sizes: [34, 33, 33],
      children: [
        { type: 'panel', id: 'systeme-etat', visible: true, closable: false },
        { type: 'panel', id: 'systeme-ressources', visible: true, closable: false },
        { type: 'panel', id: 'docker-ressources', visible: true, closable: false },
      ],
    },
  },
  'agentIde': {
    id: 'agentIde', label: 'agentIde', theme: 'dark', menu: [],
    panelTree: {
      type: 'panel', id: 'sandbox-agent-ide', visible: true, closable: false,
    },
  },
};

export function getFallbackLayout(layoutId: string): LayoutConfig {
  return FALLBACK_LAYOUTS[layoutId] || {
    id: layoutId, label: layoutId, theme: 'dark', menu: [],
    panelTree: { type: 'panel', id: 'installator-dashboard', visible: true, closable: false },
  };
}

const DEFAULT_THEME: ThemeConfig = {
  id: 'dark',
  label: 'Sombre',
  colors: {
    bg: '#1a1a2e',
    'bg-panel': '#16213e',
    fg: '#e0e0e0',
    accent: '#0f3460',
    border: '#2a2a4a',
  },
};

export function useLayout(layoutId: string) {
  const [layout, setLayout] = useState<LayoutConfig | null>(null);
  const [theme, setTheme] = useState<ThemeConfig>(DEFAULT_THEME);
  const [menu, setMenu] = useState<MenuItemDef[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const dirtyRef = useRef(false);
  const loadLayout = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    console.log('[layout] loading layout:', id);
    try {
      let data: LayoutConfig;
      try {
        const resp = await daemonPost('layout/get', { name: id });
        console.log('[layout] daemon response:', resp ? 'OK' : 'empty');
        // Le daemon enveloppe dans {ok, route, result:{name, yaml}}.
        const yaml = resp?.result?.yaml || resp?.yaml || resp;
        data = JSON.parse(yaml);
      } catch (e: any) {
        console.warn('[layout] daemon unreachable, trying ensure_daemon:', e.message);
        // Daemon indisponible → essayer de le démarrer
        try {
          const { invoke } = await import('../bridge.ts');
          const msg = await invoke('ensure_daemon');
          console.log('[layout] ensure_daemon result:', msg);
          // Réessayer après démarrage
          const resp2 = await daemonPost('layout/get', { name: id });
          const yaml2 = resp2?.result?.yaml || resp2?.yaml || resp2;
          data = JSON.parse(yaml2);
        } catch (e2: any) {
          console.warn('[layout] daemon still unreachable after ensure:', e2.message);
          // Daemon vraiment indisponible → layout de secours
          data = getFallbackLayout(id);
        }
      }
      setLayout({ ...data, panelTree: normalizeTree(data.panelTree) });

      // Charger le thème
      const themeId = data.theme || 'dark';
      try {
        const tResp = await daemonPost('theme/get', { name: themeId });
        const tYaml = tResp?.result?.yaml || tResp?.yaml || tResp;
        const tData: ThemeConfig = JSON.parse(tYaml);
        setTheme(tData);
        injectTheme(tData);
      } catch {
        setTheme(DEFAULT_THEME);
        injectTheme(DEFAULT_THEME);
      }

      // Générer le menu fusionné
      setMenu(buildMenu(data, collectPanelMenu(data.panelTree)));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadLayout(layoutId);
  }, [layoutId, loadLayout]);

  const refresh = useCallback(() => loadLayout(layoutId), [layoutId, loadLayout]);

  /** Change le layout de la fenêtre courante (nouveau layout chargé + thème). */
  const applyLayout = useCallback((newLayoutId: string) => {
    dirtyRef.current = false;
    loadLayout(newLayoutId);
  }, [loadLayout]);

  /** Change le thème courant (persiste dans le layout si dirty). */
  const applyTheme = useCallback((themeId: string) => {
    setTheme((prev) => ({ ...prev, id: themeId, label: themeId }));
    setLayout((prev) => {
      if (!prev) return prev;
      dirtyRef.current = true;
      return { ...prev, theme: themeId };
    });
    // Charge le vrai thème depuis le backend (colors/spacing/fonts).
    (async () => {
      try {
        const tResp = await daemonPost('theme/get', { name: themeId });
        const tYaml = tResp?.result?.yaml || tResp?.yaml || tResp;
        const tData: ThemeConfig = JSON.parse(tYaml);
        setTheme(tData);
        injectTheme(tData);
      } catch {
        setTheme((prev) => ({ ...prev, id: themeId, label: themeId }));
      }
    })();
  }, []);

  // Persistance automatique du layout quand l'utilisateur ajoute/retire un
  // panel (fenêtre vierge notamment). Écrit dans ~/.modelweaver/layouts/.
  useEffect(() => {
    if (!dirtyRef.current || !layout) return;
    dirtyRef.current = false;
    const persist = async () => {
      try {
        await daemonPost('layout/save', { name: layout.id, yaml: JSON.stringify(layout, null, 2) });
        logGui('layout:saved', { id: layout.id });
      } catch {}
    };
    persist();
  }, [layout]);

  /** Ajoute un panel au panelTree courant (mutations à chaud du layout).
   * S'il existe déjà un groupe d'onglets actif, l'ajoute en onglet ; sinon
   * crée un groupe racine. (Ancien comportement : premier panel = racine.) */
  const addPanel = useCallback((panelId: string) => {
    dirtyRef.current = true;
    setLayout((prev) => {
      if (!prev) return prev;
      const tree = prev.panelTree ? JSON.parse(JSON.stringify(prev.panelTree)) : null;
      // Fenêtre vierge : le premier panel devient un groupe racine.
      if (!tree) {
        return { ...prev, panelTree: makeGroup([panelId]) };
      }
      // Déjà présent → juste le rendre visible.
      const existing = findGroupWithTab(tree, panelId);
      if (existing) {
        existing.tabs = existing.tabs || [existing.id || ''];
        existing.activeTab = panelId;
        existing.visible = true;
        return { ...prev, panelTree: tree };
      }
      // Racine simple panel → on l'enveloppe en groupe puis split vertical.
      let root = tree;
      if (root.type === 'panel') {
        root = makeGroup([root.id || '']);
        root = { direction: 'vertical', sizes: [50, 50], children: [root] };
      }
      // Racine groupe sans onglets ? normaliser.
      if (root.type === 'group') {
        root.tabs = root.tabs || [root.id || ''];
        // Ajoute en onglet au groupe racine.
        if (!root.tabs.includes(panelId)) root.tabs.push(panelId);
        root.activeTab = panelId;
        return { ...prev, panelTree: root };
      }
      // Split racine : ajoute un nouveau groupe feuille (split vertical).
      root.children = root.children || [];
      root.children.push(makeGroup([panelId]));
      if (root.sizes && root.sizes.length < root.children.length) root.sizes.push(50);
      return { ...prev, panelTree: root };
    });
  }, []);

  /** Retire (masque) un panel du panelTree courant (retire l'onglet). */
  const removePanel = useCallback((panelId: string) => {
    dirtyRef.current = true;
    setLayout((prev) => {
      if (!prev || !prev.panelTree) return prev;
      const tree = JSON.parse(JSON.stringify(prev.panelTree));
      const removed = removeTabFromTree(tree, panelId);
      if (removed) return { ...prev, panelTree: tree };
      return prev;
    });
  }, []);

  /** Active un onglet dans un groupe. */
  const activateTab = useCallback((groupId: string, tabId: string) => {
    setLayout((prev) => {
      if (!prev?.panelTree) return prev;
      const tree = JSON.parse(JSON.stringify(prev.panelTree));
      const g = findGroupById(tree, groupId);
      if (g) g.activeTab = tabId;
      return { ...prev, panelTree: tree };
    });
  }, []);

  /** Ferme (retire) un onglet d'un groupe. Supprime le groupe si vide. */
  const closeTab = useCallback((groupId: string, tabId: string) => {
    dirtyRef.current = true;
    setLayout((prev) => {
      if (!prev?.panelTree) return prev;
      const tree = JSON.parse(JSON.stringify(prev.panelTree));
      const g = findGroupById(tree, groupId);
      if (!g) return prev;
      const tabs = g.tabs || [];
      const idx = tabs.indexOf(tabId);
      if (idx === -1) return prev;
      tabs.splice(idx, 1);
      if (g.activeTab === tabId) g.activeTab = tabs[Math.min(idx, tabs.length - 1)] || '';
      if (tabs.length === 0) {
        removeLeafNode(tree, groupId);
      }
      return { ...prev, panelTree: tree };
    });
  }, []);

  /** Déplace un onglet vers un autre groupe (drag/drop sur la barre). */
  const moveTabToGroup = useCallback((tabId: string, fromGroupId: string, toGroupId: string, insertIndex?: number) => {
    dirtyRef.current = true;
    setLayout((prev) => {
      if (!prev?.panelTree) return prev;
      const tree = JSON.parse(JSON.stringify(prev.panelTree));
      const fromG = findGroupById(tree, fromGroupId);
      const toG = findGroupById(tree, toGroupId);
      if (!fromG || !toG) return prev;
      const tabs = fromG.tabs || [];
      const idx = tabs.indexOf(tabId);
      if (idx === -1) return prev;
      tabs.splice(idx, 1);
      if (fromG.activeTab === tabId) fromG.activeTab = tabs[Math.min(idx, tabs.length - 1)] || '';
      if (tabs.length === 0) {
        removeLeafNode(tree, fromGroupId);
        const toG2 = findGroupById(tree, toGroupId);
        if (toG2) {
          const tt = toG2.tabs || [];
          if (insertIndex !== undefined) tt.splice(insertIndex, 0, tabId);
          else tt.push(tabId);
          toG2.tabs = tt;
          toG2.activeTab = tabId;
        }
        return { ...prev, panelTree: tree };
      }
      const tt = toG.tabs || [];
      if (insertIndex !== undefined) tt.splice(insertIndex, 0, tabId);
      else tt.push(tabId);
      toG.tabs = tt;
      toG.activeTab = tabId;
      return { ...prev, panelTree: tree };
    });
  }, []);

  /** Split d'un groupe avec un onglet déplacé (drag/drop sur les bords). */
  const splitLeafAtWithTab = useCallback((leafId: string, direction: 'horizontal' | 'vertical', tabId: string, fromGroupId: string) => {
    dirtyRef.current = true;
    setLayout((prev) => {
      if (!prev?.panelTree) return prev;
      const tree = JSON.parse(JSON.stringify(prev.panelTree));
      const fromG = findGroupById(tree, fromGroupId);
      if (!fromG) return prev;
      const tabs = fromG.tabs || [];
      const idx = tabs.indexOf(tabId);
      if (idx === -1) return prev;
      tabs.splice(idx, 1);
      if (fromG.activeTab === tabId) fromG.activeTab = tabs[Math.min(idx, tabs.length - 1)] || '';
      const newGroup = makeGroup([tabId]);
      const sourceEmpty = tabs.length === 0;
      // Groupe source vidé : on le retire (s'il est dans un children).
      if (sourceEmpty) {
        removeLeafNode(tree, fromGroupId);
      }
      // La cible du split est le groupe source (drop sur son bord) : s'il a
      // été vidé, on split la racine en {source_vide? newGroup, ...}.
      const target = findGroupById(tree, leafId);
      if (!target) {
        // Le groupe source était la racine et a été vidé → l'arbre est vide :
        // le nouveau groupe devient la racine (ou on enveloppe).
        if (sourceEmpty && tree.type === 'group' && tree.tabs?.length === 0) {
          return { ...prev, panelTree: newGroup };
        }
        return { ...prev, panelTree: tree };
      }
      const parent = findParentSplit(tree, target);
      if (parent) {
        if (parent.direction === direction) {
          parent.children.splice(parent.children.indexOf(target) + 1, 0, newGroup);
        } else {
          parent.children[parent.children.indexOf(target)] = { direction, sizes: [50, 50], children: [target, newGroup] };
        }
      } else {
        // Racine : on enveloppe. Si le groupe source a été vidé et que l'arbre
        // ne contient que le nouveau groupe, la racine devient le split
        // [newGroup] seul propre (pas de groupe vide fantôme).
        const hasEmptyGroup = tree.type === 'group' && (tree.tabs || []).length === 0;
        if (hasEmptyGroup) {
          return { ...prev, panelTree: { direction, sizes: [50, 50], children: [newGroup] } };
        }
        return { ...prev, panelTree: { direction, sizes: [50, 50], children: [tree, newGroup] } };
      }
      return { ...prev, panelTree: tree };
    });
  }, []);

  return { layout, theme, menu, loading, error, refresh, loadLayout, addPanel, removePanel, activateTab, closeTab, moveTabToGroup, splitLeafAtWithTab, applyLayout, applyTheme };
}

function genGroupId(): string {
  return `pg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

/** Crée un groupe d'onglets à partir d'ids de panels. */
function makeGroup(tabs: string[]): PanelTreeNode {
  return { type: 'group', groupId: genGroupId(), tabs: [...tabs], activeTab: tabs[0], visible: true };
}

/** Normalise un arbre chargé depuis un layout JSON : chaque nœud `panel` est
 * converti en groupe d'onglets à 1 tab (rétro-compatibilité). */
export function normalizeTree(node: PanelTreeNode | null | undefined): PanelTreeNode | null {
  if (!node) return null;
  if (node.type === 'panel' || (node.type === undefined && node.id && !node.direction && !node.children)) {
    return { type: 'group', groupId: node.groupId || genGroupId(), tabs: [node.id || ''], activeTab: node.id || '', visible: node.visible !== false, closable: node.closable };
  }
  if (node.type === 'group') {
    const tabs = (node.tabs && node.tabs.length > 0) ? node.tabs : (node.id ? [node.id] : []);
    return { ...node, type: 'group', groupId: node.groupId || genGroupId(), tabs, activeTab: node.activeTab || tabs[0], visible: node.visible !== false };
  }
  if (node.direction || node.children) {
    return { ...node, children: (node.children || []).map(normalizeTree).filter(Boolean) as PanelTreeNode[] };
  }
  return node;
}

function findGroupById(node: PanelTreeNode | null, groupId: string): PanelTreeNode | null {
  if (!node) return null;
  if (node.type === 'group' && node.groupId === groupId) return node;
  if (node.children) {
    for (const c of node.children) {
      const f = findGroupById(c, groupId);
      if (f) return f;
    }
  }
  return null;
}

function findGroupWithTab(node: PanelTreeNode | null, tabId: string): PanelTreeNode | null {
  if (!node) return null;
  if (node.type === 'group') {
    if ((node.tabs || []).includes(tabId)) return node;
  }
  if (node.children) {
    for (const c of node.children) {
      const f = findGroupWithTab(c, tabId);
      if (f) return f;
    }
  }
  return null;
}

function removeTabFromTree(node: PanelTreeNode | null, tabId: string): boolean {
  if (!node) return false;
  if (node.type === 'group') {
    const tabs = node.tabs || [];
    const idx = tabs.indexOf(tabId);
    if (idx !== -1) {
      tabs.splice(idx, 1);
      if (node.activeTab === tabId) node.activeTab = tabs[Math.min(idx, tabs.length - 1)] || '';
      return true;
    }
  }
  if (node.children) {
    for (const c of node.children) {
      if (removeTabFromTree(c, tabId)) return true;
    }
  }
  return false;
}

/** Retire un nœud feuille (groupe) de l'arbre, par son groupId. */
function removeLeafNode(node: PanelTreeNode | null, groupId: string): boolean {
  if (!node) return false;
  if (node.children) {
    const idx = node.children.findIndex((c) => c.type === 'group' && c.groupId === groupId);
    if (idx !== -1) {
      node.children.splice(idx, 1);
      if (node.sizes && node.sizes.length > node.children.length) node.sizes.splice(idx, 1);
      return true;
    }
    for (const c of node.children) {
      if (removeLeafNode(c, groupId)) return true;
    }
  }
  return false;
}

/** Trouve le split parent d'un groupe (pour insérer un nouveau split). */
function findParentSplit(node: PanelTreeNode | null, target: PanelTreeNode): { parent: PanelTreeNode; direction: string } | null {
  if (!node) return null;
  if (node.children && node.children.includes(target)) {
    return { parent: node, direction: node.direction || 'vertical' };
  }
  if (node.children) {
    for (const c of node.children) {
      const r = findParentSplit(c, target);
      if (r) return r;
    }
  }
  return null;
}

function collectPanelMenu(node?: PanelTreeNode): MenuItemDef[] {
  if (!node) return [];
  const items: MenuItemDef[] = [];
  if (node.type === 'panel' && node.id && node.visible !== false) {
    const panelDef = PANEL_REGISTRY[node.id];
    if (panelDef?.menu) items.push(...panelDef.menu);
  }
  if (node.children) {
    for (const child of node.children) {
      items.push(...collectPanelMenu(child));
    }
  }
  return items;
}

function buildMenu(layout: LayoutConfig, panelItems: MenuItemDef[]): MenuItemDef[] {
  const base = layout.menu ? JSON.parse(JSON.stringify(layout.menu)) : [];

  // Ajouter les items des panels dans le menu système
  for (const baseItem of base) {
    if (baseItem.id === 'window' && baseItem.items) {
      const toggle = baseItem.items.find((i: any) => i.type === 'panel-toggle');
      if (toggle) {
        const toggles = collectVisiblePanels(layout.panelTree);
        const sep = baseItem.items.indexOf(toggle) + 1;
        baseItem.items.splice(sep, 0, { type: 'separator' });
        baseItem.items.splice(sep + 1, 0, ...toggles.map((p) => ({
          id: `toggle:${p.id}`,
          label: p.label,
          type: 'toggle-visibility' as const,
          checked: p.visible !== false,
          action: `panel:toggle:${p.id}`,
        })));
      }
    }
  }

  // Ajouter une section par panneau pour ses items
  const hasPanelSections = base.some((i: any) => i.id === 'panels-section');
  if (!hasPanelSections && panelItems.length > 0) {
    // Regrouper les items par panneau
    base.push({ type: 'separator' });
    base.push({ id: 'panels-section', label: 'Panneaux', items: panelItems });
  }

  return base;
}

function collectVisiblePanels(node?: PanelTreeNode): { id: string; label: string; visible: boolean }[] {
  if (!node) return [];
  const result: { id: string; label: string; visible: boolean }[] = [];
  if (node.type === 'panel' && node.id) {
    const panelDef = PANEL_REGISTRY[node.id];
    result.push({
      id: node.id,
      label: panelDef?.label || node.id,
      visible: node.visible !== false,
    });
  }
  if (node.children) {
    for (const child of node.children) {
      result.push(...collectVisiblePanels(child));
    }
  }
  return result;
}

function injectTheme(theme: ThemeConfig) {
  const style = document.createElement('style');
  style.id = 'mw-theme';
  let css = ':root {\n';
  if (theme.colors) {
    for (const [k, v] of Object.entries(theme.colors)) {
      css += `  --${k}: ${v};\n`;
    }
  }
  if (theme.spacing) {
    for (const [k, v] of Object.entries(theme.spacing)) {
      css += `  --spacing-${k}: ${v};\n`;
    }
  }
  if (theme.fonts) {
    for (const [k, v] of Object.entries(theme.fonts)) {
      css += `  --font-${k}: ${v};\n`;
    }
  }
  css += '}\n';
  style.textContent = css;
  const existing = document.getElementById('mw-theme');
  if (existing) existing.remove();
  document.head.appendChild(style);
}
