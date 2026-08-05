import { useState, useEffect, useCallback, useMemo } from 'react';
import { useApp } from './useApp.ts';
import { getWindowLabel, daemonPost } from './bridge.ts';
import { useLayout } from './layout/useLayout.ts';
import { PanelTreeRenderer } from './layout/PanelTreeRenderer.tsx';
import { MenuBar } from './layout/MenuBar.tsx';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { useApp } from './useApp.ts';
import { getWindowLabel, daemonPost } from './bridge.ts';
import { useLayout } from './layout/useLayout.ts';
import { PanelTreeRenderer } from './layout/PanelTreeRenderer.tsx';
import { MenuBar } from './layout/MenuBar.tsx';
import type { MenuItemDef } from './layout/useLayout.ts';
import * as winStore from './windowStore.ts';

function LayoutWindow({ app, layoutId, windowLabel, injectedTheme }: { app: any; layoutId: string; windowLabel: string; injectedTheme: string | null }) {
  const { layout, theme, menu, loading, error, addPanel, removePanel, applyLayout, applyTheme } = useLayout(layoutId);
  const [templates, setTemplates] = useState<Record<string, any>>({});
  const [catalog, setCatalog] = useState<{ id: string; label: string }[]>([]);
  const [layouts, setLayouts] = useState<{ name: string; label: string }[]>([]);
  const [openWins, setOpenWins] = useState<winStore.WindowState[]>([]);

  // Charger templates, catalogue de panels, layouts disponibles + abonnement
  // au store des fenêtres (sync inter-fenêtres).
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const t = await daemonPost('windows/templates', {});
        if (alive) setTemplates(t?.result?.templates || t?.templates || {});
      } catch {}
      try {
        const { PANEL_REGISTRY } = await import('./panels/index.ts');
        const { loadExternalPanelIndex } = await import('./panels/loader.ts');
        const externals = await loadExternalPanelIndex();
        const all: { id: string; label: string }[] = [
          ...Object.values(PANEL_REGISTRY as Record<string, any>).map((p: any) => ({ id: p.id, label: p.label || p.id })),
          ...externals.filter((e) => !PANEL_REGISTRY[e.id]).map((e) => ({ id: e.id, label: e.label || e.id })),
        ].sort((a, b) => a.label.localeCompare(b.label));
        if (alive) setCatalog(all);
      } catch {}
      try {
        const l = await daemonPost('layout/list', {});
        const arr = l?.result?.layouts || l?.layouts || [];
        if (alive) setLayouts(arr.map((x: any) => ({ name: x.name, label: x.label || x.name })));
      } catch {}
    })();
    const unsub = winStore.subscribeWindows(setOpenWins);
    return () => { alive = false; unsub(); };
  }, []);

  // Publie l'état courant (layout/thème/titre + panels) au store partagé.
  const selfLabel = windowLabel;
  useEffect(() => {
    if (!layout || !selfLabel) return;
    const panels = winStore.collectTreePanels(layout.panelTree);
    winStore.publishLayout(selfLabel, layout.id, theme.id || 'dark', layout.label || selfLabel, panels);
  }, [layout, theme, selfLabel]);

  const handleMenuAction = useCallback(async (action: string) => {
    if (action === 'app:quit') {
      try { await daemonPost('app/quit', {}); } catch {}
      return;
    }
    if (action === 'window:fullscreen') {
      try { const { invoke } = await import('./bridge.ts'); await invoke('toggle_fullscreen'); } catch {}
      return;
    }
    if (action === 'window:close') {
      try { const { invoke } = await import('./bridge.ts'); await invoke('close_current_window'); } catch {}
      return;
    }
    if (action.startsWith('window:focus:')) {
      try {
        const { invoke } = await import('./bridge.ts');
        await invoke('focus_window', { label: action.slice('window:focus:'.length) });
      } catch {}
      return;
    }
    if (action.startsWith('window:open:')) {
      const template = action.slice('window:open:'.length);
      try {
        const resp = await daemonPost('windows/create', { template });
        const win = resp?.result?.window;
        const { createWindow } = await import('./bridge.ts');
        if (win?.window_id) await createWindow(win.window_id);
      } catch {}
      return;
    }
    if (action === 'window:open-blank') {
      try {
        const resp = await daemonPost('windows/create', { template: 'blank' });
        const win = resp?.result?.window;
        const { createWindow } = await import('./bridge.ts');
        if (win?.window_id) await createWindow(win.window_id);
      } catch {}
      return;
    }
    if (action.startsWith('window:open-saved:')) {
      const label = action.slice('window:open-saved:'.length);
      try {
        const { createWindow } = await import('./bridge.ts');
        await createWindow(label);
      } catch {}
      return;
    }
    if (action.startsWith('window:layout:')) {
      applyLayout(action.slice('window:layout:'.length));
      return;
    }
    if (action.startsWith('theme:set:')) {
      applyTheme(action.slice('theme:set:'.length));
      return;
    }
    if (action === 'window:save-all') {
      if (layout) {
        try { await daemonPost('layout/save', { name: layout.id, yaml: JSON.stringify(layout, null, 2) }); } catch {}
      }
      return;
    }
    if (action === 'window:save-layout-only') {
      if (layout) {
        const { theme: _th, ...layoutOnly } = layout;
        try { await daemonPost('layout/save', { name: layout.id, yaml: JSON.stringify(layoutOnly, null, 2) }); } catch {}
      }
      return;
    }
    if (action.startsWith('panel:add:')) {
      addPanel(action.slice('panel:add:'.length));
      return;
    }
    if (action.startsWith('panel:toggle:')) {
      const pid = action.slice('panel:toggle:'.length);
      removePanel(pid);
      return;
    }
    console.log('[App] unknown action:', action);
  }, [layout, addPanel, removePanel, applyLayout, applyTheme]);

  const ctx = { api: app, layout, theme, onMenuAction: handleMenuAction };

  // ── Menu dynamique refondu ─────────────────────────────────────────
  // Fenêtre : fenêtres ouvertes (focus), ouvrir (vierge+templates+enregistrées),
  //           enregistrer (layout+thème / layout seul), thème, choisir un layout.
  // Panneaux : de la fenêtre courante, ouverts dans une fenêtre, catalogue.
  const dynamicMenu = useMemo<MenuItemDef[]>(() => {
    const base: MenuItemDef[] = JSON.parse(JSON.stringify(menu || []));

    // ── Menu Fenêtre ──
    const windowItem: MenuItemDef = { id: 'window', label: 'Fenêtre', items: [] };
    const winItems = windowItem.items!;

    // Fenêtres ouvertes (focus au clic)
    const otherWins = openWins.filter((w) => w.label !== selfLabel);
    if (otherWins.length > 0) {
      winItems.push({
        id: 'open-windows', label: 'Fenêtres ouvertes',
        items: otherWins.map((w) => ({
          id: `focus:${w.label}`, label: `${w.title || w.label}${w.focused ? ' •' : ''}`,
          action: `window:focus:${w.label}`,
        })),
      });
      winItems.push({ type: 'separator' as const });
    }

    // Ouvrir une fenêtre : vierge d'abord, puis templates, puis enregistrées
    const openItems: MenuItemDef[] = [
      { id: 'open-blank', label: 'Fenêtre vide', action: 'window:open-blank' },
    ];
    for (const [tkey, tval] of Object.entries(templates)) {
      if (tkey === 'blank') continue;
      openItems.push({ id: `open:${tkey}`, label: tval.label || tkey, action: `window:open:${tkey}` });
    }
    const saved = openWins.filter((w) => !['installator', 'dashboard', 'agentIde'].includes(w.label));
    for (const s of saved) {
      openItems.push({ id: `open-saved:${s.label}`, label: `${s.title || s.label} (enregistrée)`, action: `window:open-saved:${s.label}` });
    }
    winItems.push({ id: 'open-window', label: 'Ouvrir une fenêtre', items: openItems });
    winItems.push({ type: 'separator' as const });

    // Enregistrer la fenêtre courante
    winItems.push({
      id: 'save-window', label: 'Enregistrer la fenêtre',
      items: [
        { id: 'save-all', label: 'Layout + thème', action: 'window:save-all' },
        { id: 'save-layout-only', label: 'Layout seul', action: 'window:save-layout-only' },
      ],
    });
    winItems.push({ type: 'separator' as const });

    // Thème (non branché aux layouts pour l'instant, juste la sélection)
    winItems.push({
      id: 'theme-menu', label: 'Thème',
      items: [
        { id: 'theme-dark', label: 'Sombre', action: 'theme:set:dark' },
        { id: 'theme-light', label: 'Clair', action: 'theme:set:light' },
      ],
    });
    winItems.push({ type: 'separator' as const });

    // Choisir un layout déjà établi
    if (layouts.length > 0) {
      winItems.push({
        id: 'choose-layout', label: 'Choisir un layout',
        items: layouts.map((l) => ({
          id: `layout:${l.name}`, label: `${l.label} (${l.name})`, action: `window:layout:${l.name}`,
        })),
      });
    }

    base.unshift(windowItem);

    // ── Menu Panneaux ──
    const panelItem: MenuItemDef = { id: 'panels-menu', label: 'Panneaux', items: [] };
    const panItems = panelItem.items!;

    // Panneaux de la fenêtre courante
    const currentPanels = winStore.collectTreePanels(layout?.panelTree);
    if (currentPanels.length > 0) {
      panItems.push({
        id: 'current-panels', label: 'De cette fenêtre',
        items: currentPanels.map((pid) => ({
          id: `toggle:${pid}`, label: catalog.find((c) => c.id === pid)?.label || pid,
          type: 'toggle-visibility' as const, checked: true, action: `panel:toggle:${pid}`,
        })),
      });
      panItems.push({ type: 'separator' as const });
    }

    // Panneaux ouverts dans une autre fenêtre
    const others = openWins.filter((w) => w.label !== selfLabel && w.panels.length > 0);
    if (others.length > 0) {
      const byWin: MenuItemDef[] = others.map((w) => ({
        id: `win-panels:${w.label}`,
        label: w.title || w.label,
        items: w.panels.map((pid) => ({
          id: `wp:${w.label}:${pid}`, label: catalog.find((c) => c.id === pid)?.label || pid,
          action: `panel:add:${pid}`,
        })),
      }));
      panItems.push({ id: 'other-windows-panels', label: 'Ouverts dans une fenêtre', items: byWin });
      panItems.push({ type: 'separator' as const });
    }

    // Catalogue non ouvert (panels pas encore dans la fenêtre courante)
    const notOpen = catalog.filter((p) => !currentPanels.includes(p.id));
    if (notOpen.length > 0) {
      panItems.push({
        id: 'catalog-panels', label: 'Catalogue (non ouvert)',
        items: notOpen.map((p) => ({
          id: `add:${p.id}`, label: p.label, action: `panel:add:${p.id}`,
        })),
      });
    }

    base.push(panelItem);

    return base;
  }, [menu, templates, catalog, layouts, openWins, selfLabel, layout]);

  if (loading) {
    return (
      <div style={{
        height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'var(--bg, #1a1a2e)', color: 'var(--fg, #e0e0e0)', fontFamily: 'sans-serif',
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>ModelWeaver</div>
          <div style={{ fontSize: '0.75rem', color: '#64748b' }}>Chargement du layout…</div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{
        height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: '#1a1a2e', color: '#e84545', fontFamily: 'sans-serif',
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>Erreur</div>
          <div style={{ fontSize: '0.75rem', color: '#fca5a5' }}>{error}</div>
        </div>
      </div>
    );
  }

  return (
    <div style={{
      height: '100vh', width: '100vw', display: 'flex', flexDirection: 'column',
      background: 'var(--bg, #0f172a)', color: 'var(--fg, #e2e8f0)',
      fontFamily: "var(--font-ui, 'Inter', system-ui, sans-serif)",
      overflow: 'hidden',
    }}>
      <style>{`@keyframes mw-spin { to { transform: rotate(360deg); } }`}</style>

      {dynamicMenu && dynamicMenu.length > 0 && (
        <MenuBar menu={dynamicMenu} onAction={handleMenuAction} />
      )}

      <div style={{ flex: 1, overflow: 'hidden' }}>
        {layout?.panelTree ? (
          <PanelTreeRenderer tree={layout.panelTree} ctx={ctx} />
        ) : (
          <div style={{
            height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
            color: '#64748b', gap: '0.5rem', fontSize: '0.8rem',
          }}>
            <div style={{ fontSize: '1.1rem', fontWeight: 600 }}>Fenêtre vierge</div>
            <div>Utilisez le menu « Panneaux → Catalogue » pour commencer.</div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const app = useApp();
  const [windowLabel, setWindowLabel] = useState<string>('installator');

  useEffect(() => {
    const fullLog = import('./gui_log.ts');
    console.log('[App] startup, hasTauri:', !!(window as any).__TAURI_INTERNALS__);
    console.log('[App] __MW_WINDOW_LABEL:', (window as any).__MW_WINDOW_LABEL);
    fullLog.then((g) => {
      g.logGui('app:startup', { hasTauri: !!(window as any).__TAURI_INTERNALS__,
                                 mwLabel: (window as any).__MW_WINDOW_LABEL || null,
                                 url: window.location.search });
    });

    // Charger l'INDEX des panels externes (compilés par panel-creator).
    // Les panels eux-mêmes sont chargés PA RESSEUSEMENT (à la demande, quand
    // une fenêtre/layout les référence) via ensurePanelLoaded.
    import('./panels/loader.ts').then((m) => {
      m.loadExternalPanelIndex().then((status) => {
        console.log(`[panels] ${status.length} panels externes indexés (paresseux)`);
        fullLog.then((g) => g.logGui('panels:index', { count: status.length }));
      }).catch((e) => { console.warn('[panels] échec index:', e); fullLog.then((g) => g.logGui('panels:index-error', String(e))); });
    });

    // Sync inter-fenêtres (écoute les broadcasts des autres Webviews) +
    // chargement des fenêtres persistées.
    import('./windowStore.ts').then((ws) => {
      ws.startWindowSync();
      ws.loadBackendWindows();
    });

    const injected = (typeof window !== 'undefined') ? (window as any).__MW_WINDOW_LABEL : null;
    if (injected) { console.log('[App] label from Rust inject:', injected); fullLog.then((g) => g.logGui('app:window-label', { source: 'inject', label: injected })); setWindowLabel(injected); return; }

    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const wl = params.get('window');
      if (wl) { console.log('[App] label from URL param:', wl); fullLog.then((g) => g.logGui('app:window-label', { source: 'url', label: wl })); setWindowLabel(wl); return; }
    }
    getWindowLabel().then((l) => { console.log('[App] label from getWindowLabel:', l); fullLog.then((g) => g.logGui('app:window-label', { source: 'tauri', label: l })); setWindowLabel(l); });
  }, []);

  // Layout + thème de la fenêtre : d'abord le profil injecté par Rust
  // (__MW_WINDOW_LAYOUT/__MW_WINDOW_THEME), sinon le mapping de repli pour
  // les fenêtres statiques. La source de vérité est le profil backend.
  const FALLBACK_LAYOUT: Record<string, string> = {
    'installator': 'default',
    'dashboard': 'dashboard',
    'agentIde': 'agentIde',
  };
  const injectedLayout = (typeof window !== 'undefined') ? (window as any).__MW_WINDOW_LAYOUT : null;
  const injectedTheme = (typeof window !== 'undefined') ? (window as any).__MW_WINDOW_THEME : null;
  const layoutId = injectedLayout || FALLBACK_LAYOUT[windowLabel] || windowLabel;
  // Trace le layout/fenêtre affiché (full-log).
  useEffect(() => {
    import('./gui_log.ts').then((g) => g.logGui('app:layout', { window: windowLabel, layout: layoutId, theme: injectedTheme || null }));
  }, [windowLabel, layoutId, injectedTheme]);

  // À l'ouverture : synchronise le profil backend (créé par la GUI ou le
  // superviseur) avec la fenêtre réelle et persiste layout/theme injectés.
  useEffect(() => {
    if (!windowLabel) return;
    const sync = async () => {
      try {
        await daemonPost('windows/create', { window_id: windowLabel, layout: layoutId, theme: injectedTheme || 'dark', visible: true });
      } catch {}
    };
    sync();
  }, [windowLabel, layoutId, injectedTheme]);

  // À la fermeture : persiste position/taille/état + retire du store.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const persist = async () => {
      try {
        const { persistWindowMetrics } = await import('./bridge.ts');
        await persistWindowMetrics();
      } catch {}
      try {
        const ws = await import('./windowStore.ts');
        ws.forgetWindow(windowLabel);
      } catch {}
    };
    window.addEventListener('beforeunload', persist);
    return () => window.removeEventListener('beforeunload', persist);
  }, [windowLabel]);

  return <LayoutWindow app={app} layoutId={layoutId} windowLabel={windowLabel} injectedTheme={injectedTheme} />;
}
