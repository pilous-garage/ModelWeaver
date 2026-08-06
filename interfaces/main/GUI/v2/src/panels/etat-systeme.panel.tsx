// Panel de base : état système (version, services, agents).

import React, { useEffect, useState } from 'react';
import type { PanelDef } from './contract.ts';
import { usePanelRefresh } from './refreshStore.ts';

const LANG_FR = `
panels:
  etat-systeme:
    titre: "État système"
    icone: "S"
    version: "Version"
    services: "Services"
    actifs: "Agents actifs"
    statut: "Statut"
    enLigne: "En ligne"
    horsLigne: "Hors ligne"
    rafraichir: "Rafraîchir"
`;

const LANG_EN = `
panels:
  etat-systeme:
    titre: "System state"
    icone: "S"
    version: "Version"
    services: "Services"
    actifs: "Agents actifs"
    statut: "Status"
    enLigne: "Online"
    horsLigne: "Hors ligne"
    rafraichir: "Refresh"
`;


interface State {
  version: string | null;
  api: string | null;
  os: string | null;
  services: number | null;
  servicesRunning: number | null;
  agents: number | null;
  agentsRunning: number | null;
  error: string | null;
}

function EtatSystemePanel({ ctx, params }: { ctx: any; params: Record<string, any> }) {
  const [state, setState] = useState<State>({ version: null, api: null, os: null, services: null, servicesRunning: null, agents: null, agentsRunning: null, error: null });

  const refresh = async () => {
    try {
      const info = await ctx.api?.post?.('system/info', {}) ?? null;
      const ver = await ctx.api?.post?.('version', {}) ?? null;
      const svc = await ctx.api?.post?.('system/services', {}) ?? null;
      const ags = await ctx.api?.post?.('agent/list', {}) ?? null;
      const infoData = info?.result ?? info ?? {};
      const verData = ver?.result ?? ver ?? {};
      const svcData = svc?.result ?? svc?.services ?? {};
      const agsData = ags?.result ?? ags ?? {};
      const services = typeof svcData === 'object' && svcData !== null ? svcData : {};
      const svcEntries = Object.values(services).filter((s: any) => s && typeof s === 'object');
      const servicesRunning_ = svcEntries.filter((s: any) => s.status === 'running').length;
      const agentsList = Array.isArray(agsData.agents) ? agsData.agents : [];
      const agentsRunning_ = agentsList.filter((a: any) => a?.running).length;
      setState({
        version: verData.version ?? verData.mw ?? infoData.version ?? '?',
        api: verData.api ?? verData.api_version ?? null,
        os: `${infoData.system ?? '?'} ${infoData.arch ?? ''}`.trim(),
        services: svcEntries.length || 0,
        servicesRunning: servicesRunning_,
        agents: agentsList.length,
        agentsRunning: agentsRunning_,
        error: null,
      });
    } catch {
      setState({ version: null, api: null, os: null, services: null, servicesRunning: null, agents: null, agentsRunning: null, error: 'erreur' });
    }
  };

  useEffect(() => { refresh(); }, []);

  // Refresh manuel (menu Affichage → Refresh).
  usePanelRefresh(ctx?.occId, refresh);

  const stat = (label: string, value: string | number | null) => (
    <div className="mw-panel-etat-stat" style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid var(--mw-border, #334155)' }}>
      <span style={{ fontSize: 12, color: 'var(--mw-fg, #94a3b8)' }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 600 }}>{value ?? '…'}</span>
    </div>
  );

  return (
    <div className="mw-panel mw-panel-etat" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span className="mw-status-dot" style={{ width: 8, height: 8, borderRadius: '50%', background: '#34d399', display: 'inline-block' }} />
        <span style={{ fontSize: 12, color: '#34d399' }}>
          {ctx.t?.('panels.etat-systeme.enLigne') ?? 'En ligne'}
        </span>
      </div>
      {stat(ctx.t?.('panels.etat-systeme.version') ?? 'Version', state.version)}
      {state.api ? stat('API', state.api) : null}
      {state.os ? stat('OS', state.os) : null}
      {stat(ctx.t?.('panels.etat-systeme.services') ?? 'Services', state.services == null ? '…' : `${state.services} (${state.servicesRunning ?? 0} run)`)}
      {stat(ctx.t?.('panels.etat-systeme.actifs') ?? 'Agents', state.agents == null ? '…' : `${state.agents} (${state.agentsRunning ?? 0} actifs)`)}
      {state.error ? <div style={{ fontSize: 11, color: '#f87171', marginTop: 6 }}>{state.error}</div> : null}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'etat-systeme',
  labelKey: 'panels.etat-systeme.titre',
  iconKey: 'panels.etat-systeme.icone',
  version: '1.0.0',
  essential: true,
  bundles: ['systeme'],
  paramsSchema: {
    compact: { type: 'boolean', default: false },
  },
  defaultParams: { compact: false },
  langFiles: [],
  themeCss: 'panels/etat-systeme/etat-systeme.panel.css',
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[etat-systeme] État système v1.0.0\n  version, services, agents\n  Params: compact=bool',
  component: EtatSystemePanel,
};
