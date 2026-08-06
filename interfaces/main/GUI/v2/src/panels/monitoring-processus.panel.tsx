// monitoring/processus — processus du système (poll 3s). Migré de V1.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll, unwrapResult } from './panel-utils.ts';

const LANG_FR = `
panels:
  monitoring-processus:
    titre: "Processus"
    pid: "PID"
    nom: "Processus"
    cpu: "CPU"
    ram: "Mémoire"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  monitoring-processus:
    titre: "Processes"
    pid: "PID"
    nom: "Processes"
    cpu: "CPU"
    ram: "Memory"
    erreur: "Error"
`;


function ProcessusPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error } = usePoll<any>(
    ctx.api.post, 'system/processes', {},
    3000,
    (res) => unwrapResult(res).processes ?? [],
  );
  const procs = data ?? [];

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.monitoring-processus.erreur') ?? 'Erreur'} : {error}</div>}
      {procs.length === 0 && !error && <div style={{ color: '#64748b' }}>…</div>}
      {procs.map((p: any, i: number) => (
        <div key={p.pid ?? i} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
          <span style={{ width: 60, color: '#64748b' }}>{p.pid}</span>
          <span style={{ flex: 1 }}>{p.name}</span>
          <span style={{ width: 55, textAlign: 'right' }}>{p.cpu_percent != null ? `${p.cpu_percent}%` : ''}</span>
          <span style={{ width: 55, textAlign: 'right' }}>{p.memory_percent != null ? `${p.memory_percent}%` : ''}</span>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'monitoring-processus',
  labelKey: 'panels.monitoring-processus.titre',
  iconKey: 'panels.monitoring-processus.titre',
  version: '1.0.0',
  essential: false,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[monitoring-processus] Processus v1.0.0\n  routes: system/processes',
  component: ProcessusPanel,
};

export const langFr = LANG_FR;