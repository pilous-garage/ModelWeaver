// installator/file-queue — file d'installation (poll 2s, via jobs/* HTTP). Migré de V1.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  installator-file-queue:
    titre: "File d'installation"
    ref: "Réf"
    type: "Type"
    statut: "Statut"
    vider: "Vider la file"
    annuler: "Annuler"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  installator-file-queue:
    titre: "Install queue"
    ref: "Ref"
    type: "Type"
    statut: "Status"
    vider: "Clear queue"
    annuler: "Cancel"
    erreur: "Error"
`;


function FileQueuePanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error, reload } = usePoll<any>(
    ctx.api.post, 'jobs/list', {},
    2000,
    (res) => unwrapResult(res).jobs ?? [],
    true,
  );
  const jobs = data ?? [];

  const cancel = async (id: number) => {
    try { await ctx.api.post('jobs/cancel', { id }); reload(); } catch { /* best-effort */ }
  };
  const clear = async () => {
    try { await ctx.api.post('jobs/clear', {}); reload(); } catch { /* best-effort */ }
  };

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <span style={{ color: '#64748b' }}>{jobs.length} job(s)</span>
        <button className="mw-btn" onClick={clear}>{ctx.t?.('panels.installator-file-queue.vider') ?? 'Vider la file'}</button>
      </div>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.installator-file-queue.erreur') ?? 'Erreur'} : {error}</div>}
      {jobs.map((j: any) => (
        <div key={j.id} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ width: 28, color: '#64748b' }}>#{j.id}</span>
          <span style={{ flex: 1 }}>{j.name ?? j.ref}</span>
          <span style={{ width: 70, color: '#94a3b8' }}>{j.job_type}</span>
          <span style={{ width: 80, color: j.status === 'running' ? '#4ade80' : '#94a3b8' }}>{j.status}</span>
          {j.status === 'running' && (
            <button className="mw-btn" onClick={() => cancel(j.id)} style={{ fontSize: 11 }}>{ctx.t?.('panels.installator-file-queue.annuler') ?? 'Annuler'}</button>
          )}
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'installator-file-queue',
  labelKey: 'panels.installator-file-queue.titre',
  iconKey: 'panels.installator-file-queue.titre',
  version: '1.0.0',
  essential: true,
  bundles: ['install'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => "[installator-file-queue] File d'installation v1.0.0\n  routes: jobs/list, jobs/cancel, jobs/clear",
  component: FileQueuePanel,
};

export const langFr = LANG_FR;