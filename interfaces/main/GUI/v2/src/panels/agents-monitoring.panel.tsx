// agents/monitoring — métriques agents + services (poll 2s, 2 appels). Migré de V1.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { useEffect, useRef, useState } from 'react';

const LANG_FR = `
panels:
  agents-monitoring:
    titre: "Monitoring agents"
    agents: "Agents"
    services: "Services"
    erreur: "Erreur"
    statut: "Statut"
`;

const LANG_EN = `
panels:
  agents-monitoring:
    titre: "Agents monitoring"
    agents: "Agents"
    services: "Services"
    erreur: "Error"
    statut: "Status"
`;


function AgentMonitoringPanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const [metrics, setMetrics] = useState<any[]>([]);
  const [services, setServices] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    const tick = async () => {
      try {
        const [m, s] = await Promise.all([
          ctx.api.post('agent/metrics', {}),
          ctx.api.post('service/resources', {}),
        ]);
        if (!alive.current) return;
        setMetrics(m?.result?.agents ?? []);
        setServices(s?.result?.services ?? []);
        setErr(null);
      } catch (e: any) {
        if (alive.current) setErr(String(e?.message ?? e));
      }
    };
    tick();
    const iv = setInterval(tick, 2000);
    return () => { alive.current = false; clearInterval(iv); };
  }, [ctx.api.post]);

  const row = (label: string, value: any) => (
    <div style={{ display: 'flex', gap: 8, padding: '2px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', fontSize: 12 }}>
      <span style={{ flex: 1 }}>{label}</span>
      <span style={{ color: '#94a3b8' }}>{value}</span>
    </div>
  );

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box' }}>
      {err && <div style={{ color: '#f87171', marginBottom: 6, fontSize: 12 }}>{ctx.t?.('panels.agents-monitoring.erreur') ?? 'Erreur'} : {err}</div>}
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>{ctx.t?.('panels.agents-monitoring.agents') ?? 'Agents'}</div>
      {metrics.map((a: any, i: number) => row(a.name ?? a.id ?? i, a.status ?? ''))}
      <div style={{ fontSize: 12, fontWeight: 600, margin: '8px 0 4px' }}>{ctx.t?.('panels.agents-monitoring.services') ?? 'Services'}</div>
      {services.map((s: any, i: number) => row(s.name ?? s.id ?? i, s.status ?? s.state ?? ''))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'agents-monitoring',
  labelKey: 'panels.agents-monitoring.titre',
  iconKey: 'panels.agents-monitoring.titre',
  version: '1.0.0',
  essential: false,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[agents-monitoring] Monitoring agents v1.0.0\n  routes: agent/metrics, service/resources',
  component: AgentMonitoringPanel,
};

export const langFr = LANG_FR;