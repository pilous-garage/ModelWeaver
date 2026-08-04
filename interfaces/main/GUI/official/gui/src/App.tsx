import { useState, useEffect, useCallback } from 'react';
import { useApp } from './useApp.ts';
import { getWindowLabel, daemonPost } from './bridge.ts';
import { useLayout } from './layout/useLayout.ts';
import { PanelTreeRenderer } from './layout/PanelTreeRenderer.tsx';
import { MenuBar } from './layout/MenuBar.tsx';

const WINDOW_TO_LAYOUT: Record<string, string> = {
  'installator': 'default',
  'dashboard': 'dashboard',
  'agentIde': 'agentIde',
};

function LayoutWindow({ app, layoutId }: { app: any; layoutId: string }) {
  const { layout, theme, menu, loading, error } = useLayout(layoutId);

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
      try { const { invoke } = await import('./bridge.ts'); await invoke('close_window'); } catch {}
      return;
    }
    if (action === 'layout:save') {
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
  }, [layout]);

  const ctx = { api: app, layout, theme, onMenuAction: handleMenuAction };

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

      {menu && menu.length > 0 && (
        <MenuBar menu={menu} onAction={handleMenuAction} />
      )}

      <div style={{ flex: 1, overflow: 'hidden' }}>
        {layout?.panelTree ? (
          <PanelTreeRenderer tree={layout.panelTree} ctx={ctx} />
        ) : (
          <div style={{ padding: '1rem', color: '#fca5a5' }}>Aucun panelTree dans le layout</div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const app = useApp();
  const [windowLabel, setWindowLabel] = useState<string>('installator');

  useEffect(() => {
    console.log('[App] startup, hasTauri:', !!(window as any).__TAURI_INTERNALS__);
    console.log('[App] __MW_WINDOW_LABEL:', (window as any).__MW_WINDOW_LABEL);

    // Charger l'INDEX des panels externes (compilés par panel-creator).
    // Les panels eux-mêmes sont chargés PA RESSEUSEMENT (à la demande, quand
    // une fenêtre/layout les référence) via ensurePanelLoaded.
    import('./panels/loader.ts').then((m) => {
      m.loadExternalPanelIndex().then((status) => {
        console.log(`[panels] ${status.length} panels externes indexés (paresseux)`);
      }).catch((e) => console.warn('[panels] échec index:', e));
    });

    const injected = (typeof window !== 'undefined') ? (window as any).__MW_WINDOW_LABEL : null;
    if (injected) { console.log('[App] label from Rust inject:', injected); setWindowLabel(injected); return; }

    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const wl = params.get('window');
      if (wl) { console.log('[App] label from URL param:', wl); setWindowLabel(wl); return; }
    }
    getWindowLabel().then((l) => { console.log('[App] label from getWindowLabel:', l); setWindowLabel(l); });
  }, []);

  const layoutId = WINDOW_TO_LAYOUT[windowLabel] || 'default';
  return <LayoutWindow app={app} layoutId={layoutId} />;
}
