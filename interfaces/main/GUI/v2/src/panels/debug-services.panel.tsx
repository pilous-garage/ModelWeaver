// debug/services — supervision des services du daemon. Migré de V1.
// Routes : service/list (poll 3s), service/restart, service/stop, service/start.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  debug-services:
    titre: "Services"
    statut: "Statut"
    mode: "Mode"
    pid: "PID"
    redem: "Redém."
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  debug-services:
    titre: "Services"
    statut: "Status"
    mode: "Mode"
    pid: "PID"
    redem: "Restart"
    erreur: "Error"
`;


function ServicesPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error, reload } = usePoll<any>(
    ctx.api.post, 'service/list', {}, 3000,
    (res) => unwrapResult(res).services ?? [],
    true,
  );
  const services = data ?? [];

  const act = async (action: string, name: string) => {
    try { await ctx.api.post(`service/${action}`, { name }); reload(); } catch { /* best-effort */ }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.debug-services.erreur') ?? 'Erreur'} : {error}</div>}
      {services.length === 0 && !error && <div style={{ color: '#64748b' }}>…</div>}
      {services.map((s: any) => (
        <div key={s.name} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ flex: 1, fontWeight: 600 }}>{s.name}</span>
          <span style={{ width: 70, color: '#94a3b8' }}>{s.mode ?? ''}</span>
          <span style={{ width: 70, color: s.status === 'running' ? '#4ade80' : s.status === 'crashed' ? '#f87171' : '#f59e0b' }}>{s.status ?? ''}</span>
          <span style={{ width: 45, textAlign: 'right', color: '#64748b' }}>{s.pid ?? '—'}</span>
          <span style={{ width: 45, textAlign: 'right', color: '#64748b' }}>{s.restarts ?? 0}</span>
          {s.status === 'running' ? (
            <button className="mw-btn" onClick={() => act('restart', s.name)} style={{ fontSize: 11 }} title="Redémarrer">⟳</button>
          ) : (
            <button className="mw-btn" onClick={() => act('start', s.name)} style={{ fontSize: 11 }} title="Démarrer">▶</button>
          )}
          <button className="mw-btn" onClick={() => act('stop', s.name)} style={{ fontSize: 11 }} title="Arrêter">■</button>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'debug-services',
  labelKey: 'panels.debug-services.titre',
  iconKey: 'panels.debug-services.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['debug'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[debug-services] Services v1.0.0\n  routes: service/list, service/restart, service/stop, service/start',
  component: ServicesPanel,
};

export const langFr = LANG_FR;