/** useLayout — Hook de chargement du layout, thème, menu */

import { useState, useEffect, useCallback } from 'react';
import { daemonPost } from '../bridge.ts';
import { PANEL_REGISTRY } from '../panels/index.ts';

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

  // Charger le layout depuis le daemon
  const loadLayout = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      let data: LayoutConfig;
      try {
        const resp = await daemonPost('layout/get', { name: id });
        data = JSON.parse(resp.yaml || resp);
      } catch {
        // Daemon indisponible → essayer de le démarrer
        try {
          const { invoke } = await import('../bridge.ts');
          const msg = await invoke('ensure_daemon');
          console.log('[layout]', msg);
          // Réessayer après démarrage
          const resp2 = await daemonPost('layout/get', { name: id });
          data = JSON.parse(resp2.yaml || resp2);
        } catch {
          // Daemon vraiment indisponible → layout minimal de secours
          data = {
            id: id, label: id, theme: 'dark', menu: [],
            panelTree: {
              type: 'panel', id: 'installator-dashboard', visible: true, closable: false,
            },
          };
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

  return { layout, theme, menu, loading, error, refresh, loadLayout };
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
