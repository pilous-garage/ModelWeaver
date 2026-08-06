// installator/dashboard — vue d'ensemble (état système + ressources). Migré de V1.
// Compose les deux sources HTTP : system/hardware (one-shot) + system/resources (poll 2s).

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  installator-dashboard:
    titre: "Dashboard"
    rafraichir: "Rafraîchir"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  installator-dashboard:
    titre: "Dashboard"
    rafraichir: "Refresh"
    erreur: "Error"
`;


function DashboardPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data: hw } = usePoll<any>(
    ctx.api.post, 'system/hardware', {}, 60000, unwrapResult,
  );
  const { data: res, error } = usePoll<any>(
    ctx.api.post, 'system/resources', {}, 2000, unwrapResult,
  );

  const row = (label: string, value: any) => (
    <div style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', fontSize: 12 }}>
      <span style={{ width: 110, color: '#64748b' }}>{label}</span>
      <span style={{ flex: 1 }}>{value ?? '—'}</span>
    </div>
  );

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box' }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6, fontSize: 12 }}>{ctx.t?.('panels.installator-dashboard.erreur') ?? 'Erreur'} : {error}</div>}
      {row('CPU', hw?.cpu?.name ?? hw?.cpu?.model)}
      {row('RAM', hw?.memory?.total ? `${(hw.memory.total / (1024 ** 3)).toFixed(1)} Go` : null)}
      {row('GPU', Array.isArray(hw?.gpus) ? hw.gpus.map((g: any) => g.name ?? g.model).join(', ') : null)}
      {row('Disques', Array.isArray(hw?.disks) ? hw.disks.length : null)}
      {Array.isArray(res?.gpus) && res.gpus.map((g: any, i: number) => (
        row(`GPU ${i + 1} usage`, g.utilization != null ? `${g.utilization}%` : null)
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'installator-dashboard',
  labelKey: 'panels.installator-dashboard.titre',
  iconKey: 'panels.installator-dashboard.titre',
  version: '1.0.0',
  essential: true,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[installator-dashboard] Dashboard v1.0.0\n  routes: system/hardware, system/resources',
  component: DashboardPanel,
};

export const langFr = LANG_FR;