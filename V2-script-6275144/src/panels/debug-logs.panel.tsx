// debug/logs — logs, processus, services. Migré de V1 (version découplée).
// Routes : system/processes (poll 3s), service/list (poll 5s), logs/read.

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  debug-logs:
    titre: "Debug"
    processus: "Processus"
    services: "Services"
    logs: "Logs"
    nom: "Nom"
    cpu: "CPU"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  debug-logs:
    titre: "Debug"
    processus: "Processes"
    services: "Services"
    logs: "Logs"
    nom: "Name"
    cpu: "CPU"
    erreur: "Error"
`;


type Tab = 'processus' | 'services' | 'logs';

function DebugPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const [tab, setTab] = useState<Tab>('processus');
  const { data: procs } = usePoll<any>(
    ctx.api.post, 'system/processes', {}, 3000,
    (res) => res?.result?.processes ?? [], true,
  );
  const { data: services } = usePoll<any>(
    ctx.api.post, 'service/list', {}, 5000,
    (res) => res?.result?.services ?? [], true,
  );
  const { data: log } = usePoll<any>(
    ctx.api.post, 'logs/read', {}, 5000,
    (res) => res?.result?.log ?? '', true,
  );

  const btn = (t: Tab, label: string) => (
    <button onClick={() => setTab(t)} style={{ padding: '3px 8px', fontSize: 11, borderRadius: '4px 4px 0 0', border: 'none', cursor: 'pointer', background: tab === t ? 'var(--mw-accent, #0f3460)' : '#334155', color: tab === t ? '#e2e8f0' : '#94a3b8' }}>{label}</button>
  );

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'hidden', padding: 6, boxSizing: 'border-box', fontSize: 12, display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', gap: 2, marginBottom: 4 }}>
        {btn('processus', ctx.t?.('panels.debug-logs.processus') ?? 'Processus')}
        {btn('services', ctx.t?.('panels.debug-logs.services') ?? 'Services')}
        {btn('logs', ctx.t?.('panels.debug-logs.logs') ?? 'Logs')}
      </div>
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        {tab === 'processus' && (procs ?? []).map((p: any) => (
          <div key={p.pid ?? p.name} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
            <span style={{ width: 60, color: '#64748b' }}>{p.pid}</span>
            <span style={{ flex: 1 }}>{p.name}</span>
            <span style={{ color: '#94a3b8' }}>{p.cpu != null ? `${p.cpu.toFixed?.(1) ?? p.cpu}%` : ''}</span>
          </div>
        ))}
        {tab === 'services' && (services ?? []).map((s: any) => (
          <div key={s.name} style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
            <span style={{ flex: 1 }}>{s.name}</span>
            <span style={{ color: s.status === 'running' ? '#4ade80' : '#94a3b8' }}>{s.status}</span>
          </div>
        ))}
        {tab === 'logs' && (
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 11, color: '#94a3b8', margin: 0 }}>{(log ?? '').slice(-4000)}</pre>
        )}
      </div>
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'debug-logs',
  labelKey: 'panels.debug-logs.titre',
  iconKey: 'panels.debug-logs.titre',
  version: '1.0.0',
  essential: false,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[debug-logs] Debug v1.0.0\n  routes: system/processes, service/list, logs/read',
  component: DebugPanel,
};

export const langFr = LANG_FR;