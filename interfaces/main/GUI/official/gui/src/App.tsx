import { useState, useEffect, useCallback, useMemo } from 'react';
import { useApp } from './useApp.ts';
import { getWindowLabel, daemonPost } from './bridge.ts';
import { useLayout } from './layout/useLayout.ts';
import { PanelTreeRenderer } from './layout/PanelTreeRenderer.tsx';
import { MenuBar } from './layout/MenuBar.tsx';

function LayoutWindow({ app, layoutId }: { app: any; layoutId: string }) {
  const { layout, theme, menu, loading, error, addPanel, removePanel } = useLayout(layoutId);
  const [templates, setTemplates] = useState<Record<string, any>>({});
  const [catalog, setCatalog] = useState<{ id: string; label: string }[]>([]);

  // Charger les templates de fenêtres + le catalogue complet des panels
  // (essentiels + externes indexés) pour le menu "Nouvelle fenêtre"/"Ajouter".
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
    })();
    return () => { alive = false; };
  }, []);

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
    if (action.startsWith('panel:add:')) {
      addPanel(action.slice('panel:add:'.length));
      return;
    }
    if (action.startsWith('panel:toggle:')) {
      const pid = action.slice('panel:toggle:'.length);
      removePanel(pid);
      return;
    }    if (action === 'layout:save') {
      if (layout) {
        try { await daemonPost('layout/save', { name: layout.id, yaml: JSON.stringify(layout, null, 2) }); } catch {}
      }
      return;
    }
    if (action.startsWith('theme:set:')) {
      await daemonPost('theme/get', { name: action.split(':')[2] });
      return;
    }
    if (action.startsWith('panel:')) {
      console.log('[App] menu action:', action);
      return;
    }
    console.log('[App] unknown action:', action);
  }, [layout, addPanel, removePanel]);

  const ctx = { api: app, layout, theme, onMenuAction: handleMenuAction };

  // Enrichit le menu chargé avec les sections dynamiques :
  //  - Fenêtre → Nouvelle fenêtre (templates + vierge)
  //  - Fenêtre → Ajouter un panneau (catalogue complet : essentiels + externes)
  const [menuExtra, setMenuExtra] = useState<{ id: string; label: string }[]>([]);
  const [tplExtra, setTplExtra] = useState<Record<string, any>>({});
  useEffect(() => {
    setMenuExtra(catalog);
    setTplExtra(templates);
  }, [catalog, templates]);

  const dynamicMenu = useMemo(() => {
    const base = JSON.parse(JSON.stringify(menu || []));
    const windowItem = base.find((i: any) => i.id === 'window') || {
      id: 'window', label: 'Fenêtre', items: [],
    };
    if (!base.includes(windowItem)) base.unshift(windowItem);
    const winItems = windowItem.items || (windowItem.items = []);
    // Groupe "Nouvelle fenêtre"
    const newWin: any[] = [];
    for (const [tkey, tval] of Object.entries(tplExtra)) {
      newWin.push({ id: `open:${tkey}`, label: tval.label || tkey, action: tkey === 'blank' ? 'window:open-blank' : `window:open:${tkey}` });
    }
    if (newWin.length > 0) {
      winItems.unshift({ id: 'new-window', label: 'Nouvelle fenêtre', items: newWin });
      winItems.splice(1, 0, { type: 'separator' });
    }
    // Groupe "Ajouter un panneau" (catalogue complet)
    if (menuExtra.length > 0) {
      const addItems = menuExtra.map((p) => ({
        id: `add:${p.id}`, label: p.label, action: `panel:add:${p.id}`,
      }));
      winItems.push({ type: 'separator' });
      winItems.push({ id: 'add-panel', label: 'Ajouter un panneau', items: addItems });
    }
    // Si le layout est vierge (pas de panelTree), propose aussi un layout de
    // départ classique.
    return base;
  }, [menu, menuExtra, tplExtra]);

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
            <div>Utilisez le menu « Fenêtre → Ajouter un panneau » pour commencer.</div>
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

  // À la fermeture : persiste position/taille/état via get_window_metrics +
  // windows/update (async best-effort avant unload).
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const persist = async () => {
      try {
        const { persistWindowMetrics } = await import('./bridge.ts');
        await persistWindowMetrics();
      } catch {}
    };
    window.addEventListener('beforeunload', persist);
    return () => window.removeEventListener('beforeunload', persist);
  }, []);

  return <LayoutWindow app={app} layoutId={layoutId} />;
}
