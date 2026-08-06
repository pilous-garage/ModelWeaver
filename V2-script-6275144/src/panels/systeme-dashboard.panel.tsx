// systeme/dashboard — vue d'ensemble (compose les panels V2 essentiels). Migré de V1.
// Réutilise les panels V2 déjà migrés en onglets rapides.

import React, { useState } from 'react';
import type { PanelDef } from './contract.ts';

const LANG_FR = `
panels:
  systeme-dashboard:
    titre: "Dashboard"
    systeme: "Système"
    ressources: "Ressources"
    services: "Services"
    agents: "Agents"
`;

const LANG_EN = `
panels:
  systeme-dashboard:
    titre: "Dashboard"
    systeme: "Système"
    ressources: "Resources"
    services: "Services"
    agents: "Agents"
`;


function DashboardPanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const [tab, setTab] = useState<string>(params.tab ?? 'systeme');
  const tabs = [
    { id: 'systeme', label: ctx.t?.('panels.systeme-dashboard.systeme') ?? 'Système' },
    { id: 'ressources', label: ctx.t?.('panels.systeme-dashboard.ressources') ?? 'Ressources' },
    { id: 'services', label: ctx.t?.('panels.systeme-dashboard.services') ?? 'Services' },
    { id: 'agents', label: ctx.t?.('panels.systeme-dashboard.agents') ?? 'Agents' },
  ];
  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      <div style={{ display: 'flex', gap: 4, marginBottom: 8, flexWrap: 'wrap' }}>
        {tabs.map((t) => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{ padding: '3px 8px', fontSize: 11, borderRadius: 4, border: 'none', cursor: 'pointer', background: tab === t.id ? 'var(--mw-accent, #0f3460)' : '#334155', color: tab === t.id ? '#e2e8f0' : '#94a3b8' }}>{t.label}</button>
        ))}
      </div>
      {tab === 'systeme' && <SystemeBrief ctx={ctx} />}
      {tab === 'ressources' && <RessourcesBrief ctx={ctx} />}
      {tab === 'services' && <ServicesBrief ctx={ctx} />}
      {tab === 'agents' && <AgentsBrief ctx={ctx} />}
    </div>
  );
}

function SystemeBrief({ ctx }: { ctx: any }) {
  const [hw, setHw] = React.useState<any>(null);
  React.useEffect(() => {
    let alive = true;
    ctx.api.post('system/hardware', {}).then((res: any) => { if (alive) setHw(res?.result ?? {}); }).catch(() => {});
    return () => { alive = false; };
  }, [ctx.api.post]);
  return (
    <div>
      <div style={{ display: 'flex', gap: 8, padding: '2px 0' }}><span style={{ width: 90, color: '#64748b' }}>CPU</span><span>{hw?.cpu?.name ?? hw?.cpu?.model ?? '—'}</span></div>
      <div style={{ display: 'flex', gap: 8, padding: '2px 0' }}><span style={{ width: 90, color: '#64748b' }}>RAM</span><span>{hw?.memory?.total ? `${(hw.memory.total / 1073741824).toFixed(1)} Go` : '—'}</span></div>
      <div style={{ display: 'flex', gap: 8, padding: '2px 0' }}><span style={{ width: 90, color: '#64748b' }}>GPU</span><span>{Array.isArray(hw?.gpus) ? hw.gpus.map((g: any) => g.name ?? g.model).join(', ') : '—'}</span></div>
    </div>
  );
}

function RessourcesBrief({ ctx }: { ctx: any }) {
  const [res, setRes] = React.useState<any>(null);
  React.useEffect(() => {
    let alive = true;
    const tick = () => ctx.api.post('system/resources', {}).then((r: any) => { if (alive) setRes(r?.result ?? {}); }).catch(() => {});
    tick();
    const iv = setInterval(tick, 2000);
    return () => { alive = false; clearInterval(iv); };
  }, [ctx.api.post]);
  return (
    <div>
      {(res?.gpus ?? []).map((g: any, i: number) => (
        <div key={i} style={{ display: 'flex', gap: 8, padding: '2px 0' }}><span style={{ flex: 1 }}>{g.name ?? g.model}</span><span style={{ color: '#94a3b8' }}>{g.busy_percent != null ? `${g.busy_percent}%` : ''}</span></div>
      ))}
      {(res?.gpus ?? []).length === 0 && <div style={{ color: '#64748b' }}>…</div>}
    </div>
  );
}

function ServicesBrief({ ctx }: { ctx: any }) {
  const [services, setServices] = React.useState<any[]>([]);
  React.useEffect(() => {
    let alive = true;
    const tick = () => ctx.api.post('service/list', {}).then((r: any) => { if (alive) setServices(r?.result?.services ?? []); }).catch(() => {});
    tick();
    const iv = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(iv); };
  }, [ctx.api.post]);
  return (
    <div>
      {services.map((s: any) => (
        <div key={s.name} style={{ display: 'flex', gap: 8, padding: '2px 0' }}>
          <span style={{ flex: 1 }}>{s.name}</span>
          <span style={{ color: s.status === 'running' ? '#4ade80' : '#94a3b8' }}>{s.status}</span>
        </div>
      ))}
      {services.length === 0 && <div style={{ color: '#64748b' }}>…</div>}
    </div>
  );
}

function AgentsBrief({ ctx }: { ctx: any }) {
  const [data, setData] = React.useState<any>(null);
  React.useEffect(() => {
    let alive = true;
    const tick = () => ctx.api.post('agent/list-by-team', {}).then((r: any) => { if (alive) setData(r?.result ?? {}); }).catch(() => {});
    tick();
    const iv = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(iv); };
  }, [ctx.api.post]);
  const all = [...Object.values(data?.teams ?? {}).flatMap((t: any) => t?.agents ?? []), ...(data?.standalone ?? [])];
  return (
    <div>
      <div style={{ color: '#64748b', marginBottom: 4 }}>{all.length} agent(s)</div>
      {all.slice(0, 20).map((a: any) => (
        <div key={a.agent_id ?? a.name} style={{ display: 'flex', gap: 8, padding: '1px 0' }}>
          <span style={{ flex: 1 }}>{a.name}</span>
          <span style={{ color: a.running ? '#4ade80' : '#64748b' }}>{a.running ? '●' : a.status}</span>
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'systeme-dashboard',
  labelKey: 'panels.systeme-dashboard.titre',
  iconKey: 'panels.systeme-dashboard.titre',
  version: '1.0.0',
  essential: false,
  paramsSchema: {
    tab: { type: 'enum', enum: ['systeme', 'ressources', 'services', 'agents'], default: 'systeme' },
  },
  defaultParams: { tab: 'systeme' },
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[systeme-dashboard] Dashboard v1.0.0\n  routes: system/hardware, system/resources, service/list, agent/list-by-team',
  component: DashboardPanel,
};

export const langFr = LANG_FR;