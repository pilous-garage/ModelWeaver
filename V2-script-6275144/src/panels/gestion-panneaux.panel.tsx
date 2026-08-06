// gestion/panneaux — gestion des panels et fenêtres. Migré de V1.
// Routes : panels/index, windows/list, windows/templates, windows/create, windows/close.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  gestion-panneaux:
    titre: "Gestion panneaux"
    fenetres: "Fenêtres"
    panels: "Panels"
    ouvrir: "Ouvrir"
    fermer: "Fermer"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  gestion-panneaux:
    titre: "Gestion panneaux"
    fenetres: "Fenêtres"
    panels: "Panels"
    ouvrir: "Ouvrir"
    fermer: "Fermer"
    erreur: "Error"
`;


function PanneauxPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data: windows, reload: reloadWin } = usePoll<any>(
    ctx.api.post, 'windows/list', {}, 5000,
    (res) => res?.result?.windows ?? [], true,
  );
  const { data: panels } = usePoll<any>(
    ctx.api.post, 'panels/index', {}, 15000,
    (res) => res?.result?.panels ?? [], true,
  );

  const openWindow = async (template: string) => {
    try { await ctx.api.post('windows/create', { template }); reloadWin(); } catch { /* best-effort */ }
  };
  const closeWindow = async (windowId: string) => {
    try { await ctx.api.post('windows/close', { window_id: windowId }); reloadWin(); } catch { /* best-effort */ }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ fontWeight: 600, margin: '4px 0' }}>{ctx.t?.('panels.gestion-panneaux.fenetres') ?? 'Fenêtres'}</div>
      {(windows ?? []).map((w: any) => (
        <div key={w.window_id} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ flex: 1, fontWeight: 600 }}>{w.title || w.window_id}</span>
          <span style={{ color: '#64748b' }}>{w.layout ?? ''}</span>
          <button className="mw-btn" onClick={() => closeWindow(w.window_id)} style={{ fontSize: 11 }}>{ctx.t?.('panels.gestion-panneaux.fermer') ?? 'Fermer'}</button>
        </div>
      ))}
      <div style={{ fontWeight: 600, margin: '8px 0 4px' }}>{ctx.t?.('panels.gestion-panneaux.panels') ?? 'Panels'} ({panels?.length ?? 0})</div>
      {(panels ?? []).slice(0, 50).map((p: any) => (
        <div key={p.id} style={{ padding: '1px 0', color: '#94a3b8', fontSize: 11 }}>{p.id} <span style={{ color: '#64748b' }}>v{p.version ?? ''}</span></div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'gestion-panneaux',
  labelKey: 'panels.gestion-panneaux.titre',
  iconKey: 'panels.gestion-panneaux.titre',
  version: '1.0.0',
  essential: true,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[gestion-panneaux] Gestion panneaux v1.0.0\n  routes: panels/index, windows/list, windows/templates, windows/create, windows/close',
  component: PanneauxPanel,
};

export const langFr = LANG_FR;