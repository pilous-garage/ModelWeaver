// agents/liste — liste des agents par team. Migré de V1.
// Routes : agent/list-by-team (poll 3s), agent/signal, agent/stop, agent/restart.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  agents-liste:
    titre: "Agents"
    statut: "Statut"
    role: "Rôle"
    occ: "Occupation"
    stop: "Arrêter"
    restart: "Redémarrer"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  agents-liste:
    titre: "Agents"
    statut: "Status"
    role: "Rôle"
    occ: "Occupation"
    stop: "Arrêter"
    restart: "Redémarrer"
    erreur: "Error"
`;


function AgentsListePanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error, reload } = usePoll<any>(
    ctx.api.post, 'agent/list-by-team', {}, 3000,
    (res) => res?.result ?? {}, true,
  );
  const teams = data?.teams ?? {};
  const standalone = data?.standalone ?? [];

  const act = async (action: string, name: string) => {
    try { await ctx.api.post(action, { name }); reload(); } catch { /* best-effort */ }
  };
  const signal = async (agentId: string, type: string) => {
    try { await ctx.api.post('agent/signal', { agent_id: agentId, type, payload: {} }); reload(); } catch { /* best-effort */ }
  };

  const renderAgent = (a: any) => (
    <div key={a.agent_id ?? a.name} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
      <span style={{ flex: 1, fontWeight: 600 }}>{a.name}</span>
      <span style={{ width: 90, color: '#94a3b8' }}>{a.role_type ?? ''}</span>
      <span style={{ width: 80, color: a.running ? '#4ade80' : a.status === 'INIT' ? '#f59e0b' : '#64748b' }}>{a.running ? '●' : a.status}</span>
      <button className="mw-btn" onClick={() => act('agent/restart', a.name)} style={{ fontSize: 11 }}>{ctx.t?.('panels.agents-liste.restart') ?? 'Redémarrer'}</button>
      <button className="mw-btn" onClick={() => act('agent/stop', a.name)} style={{ fontSize: 11 }}>{ctx.t?.('panels.agents-liste.stop') ?? 'Arrêter'}</button>
    </div>
  );

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.agents-liste.erreur') ?? 'Erreur'} : {error}</div>}
      {Object.entries(teams).map(([team, t]: [string, any]) => (
        <div key={team} style={{ marginBottom: 8 }}>
          <div style={{ fontWeight: 600, color: '#93c5fd', marginBottom: 2 }}>{team}</div>
          {(t?.agents ?? []).map(renderAgent)}
        </div>
      ))}
      {standalone.map(renderAgent)}
      {Object.keys(teams).length === 0 && standalone.length === 0 && !error && <div style={{ color: '#64748b' }}>…</div>}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'agents-liste',
  labelKey: 'panels.agents-liste.titre',
  iconKey: 'panels.agents-liste.titre',
  version: '1.0.0',
  essential: true,
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[agents-liste] Agents v1.0.0\n  routes: agent/list-by-team, agent/signal, agent/stop, agent/restart',
  component: AgentsListePanel,
};

export const langFr = LANG_FR;