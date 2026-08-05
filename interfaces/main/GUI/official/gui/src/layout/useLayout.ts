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
  type?: 'panel' | 'ext';
  id?: string;
  url?: string;
  visible?: boolean;
  closable?: boolean;
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
        data = JSON.parse(resp.yaml || resp);
      } catch (e: any) {
        console.warn('[layout] daemon unreachable, trying ensure_daemon:', e.message);
        // Daemon indisponible → essayer de le démarrer
        try {
          const { invoke } = await import('../bridge.ts');
          const msg = await invoke('ensure_daemon');
          console.log('[layout] ensure_daemon result:', msg);
          // Réessayer après démarrage
          const resp2 = await daemonPost('layout/get', { name: id });
          data = JSON.parse(resp2.yaml || resp2);
        } catch (e2: any) {
          console.warn('[layout] daemon still unreachable after ensure:', e2.message);
          // Daemon vraiment indisponible → layout de secours
          data = getFallbackLayout(id);
        }
      }
      setLayout(data);

      // Charger le thème
      const themeId = data.theme || 'dark';
      try {
        const tResp = await daemonPost('theme/get', { name: themeId });
        const tData: ThemeConfig = JSON.parse(tResp.yaml || tResp);
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
        const tData: ThemeConfig = JSON.parse(tResp.yaml || tResp);
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

  /** Ajoute un panel au panelTree courant (mutations à chaud du layout). */
  const addPanel = useCallback((panelId: string) => {
    dirtyRef.current = true;
    setLayout((prev) => {
      if (!prev) return prev;
      const tree = prev.panelTree ? JSON.parse(JSON.stringify(prev.panelTree)) : null;
      // Fenêtre vierge : le premier panel devient la racine.
      if (!tree) {
        return { ...prev, panelTree: { type: 'panel', id: panelId, visible: true, closable: true } };
      }
      // Si la racine est un simple panel, on l'enveloppe en splitter vertical.
      let root = tree;
      if (root.type === 'panel') {
        root = { direction: 'vertical', sizes: [50, 50], children: [root] };
      }
      // Anti-doublon : panel déjà présent → juste le rendre visible.
      const exists = findPanel(root, panelId);
      if (exists) {
        exists.visible = true;
        return { ...prev, panelTree: root };
      }
      root.children = root.children || [];
      root.children.push({ type: 'panel', id: panelId, visible: true, closable: true });
      return { ...prev, panelTree: root };
    });
  }, []);

  /** Retire (masque) un panel du panelTree courant. */
  const removePanel = useCallback((panelId: string) => {
    dirtyRef.current = true;
    setLayout((prev) => {
      if (!prev || !prev.panelTree) return prev;
      const tree = JSON.parse(JSON.stringify(prev.panelTree));
      const removed = hidePanel(tree, panelId);
      if (removed) return { ...prev, panelTree: tree };
      return prev;
    });
  }, []);

  return { layout, theme, menu, loading, error, refresh, loadLayout, addPanel, removePanel, applyLayout, applyTheme };
}

function findPanel(node: PanelTreeNode | undefined, id: string): PanelTreeNode | null {
  if (!node) return null;
  if (node.type === 'panel' && node.id === id) return node;
  if (node.children) {
    for (const c of node.children) {
      const f = findPanel(c, id);
      if (f) return f;
    }
  }
  return null;
}

function hidePanel(node: PanelTreeNode | undefined, id: string): boolean {
  if (!node) return false;
  if (node.type === 'panel' && node.id === id) {
    node.visible = false;
    return true;
  }
  if (node.children) {
    for (const c of node.children) {
      if (hidePanel(c, id)) return true;
    }
  }
  return false;
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
