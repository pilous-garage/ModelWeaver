// swarm/taskflow — graphe du taskflow de TOUTES les teams (teamflow + swarmflow).
// Sources : agent/taskflow (build_taskflow sans team = toutes).
// Vue : liste des rôles avec leurs routes consomme/génère, groupés par team.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  swarm-taskflow:
    titre: "Swarmflow"
    roles: "Rôles"
    types: "Types de tâches"
    consomme: "Consomme"
    genere: "Génère"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  swarm-taskflow:
    titre: "Swarmflow"
    roles: "Roles"
    types: "Task types"
    consomme: "Consumes"
    genere: "Generates"
    erreur: "Error"
`;


function SwarmTaskflowPanel({ ctx }: { ctx: any }) {
  const { data, error } = usePoll<any>(
    ctx.api.post, 'agent/taskflow', {}, 8000,
    (res) => res?.result ?? {}, true,
  );
  const nodes: any[] = data?.nodes ?? [];
  const taskTypes: Record<string, any> = data?.task_types ?? {};

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.swarm-taskflow.erreur') ?? 'Erreur'} : {error}</div>}

      <div style={{ fontWeight: 700, margin: '4px 0', color: '#a5b4fc' }}>{ctx.t?.('panels.swarm-taskflow.roles') ?? 'Rôles'} ({nodes.length})</div>
      {nodes.map((n: any) => (
        <div key={n.id} style={{ marginBottom: 4, border: '1px solid var(--mw-border, #1e293b)', borderRadius: 6, padding: '5px 8px' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span style={{ fontWeight: 700, width: 130 }}>{n.label ?? n.id}</span>
            <span style={{ color: '#64748b', fontSize: 11 }}>({n.agents?.length ?? 0} agents)</span>
          </div>
          <div style={{ display: 'flex', gap: 16, marginTop: 3, fontSize: 11 }}>
            <div>
              <span style={{ color: '#fbbf24' }}>{ctx.t?.('panels.swarm-taskflow.consomme') ?? 'Consomme'}: </span>
              <span style={{ color: '#cbd5e1' }}>{(n.consumes ?? []).join(', ') || '—'}</span>
            </div>
            <div>
              <span style={{ color: '#4ade80' }}>{ctx.t?.('panels.swarm-taskflow.genere') ?? 'Génère'}: </span>
              <span style={{ color: '#cbd5e1' }}>{(n.generates ?? []).join(', ') || '—'}</span>
            </div>
          </div>
        </div>
      ))}

      <div style={{ fontWeight: 700, margin: '10px 0 4px', color: '#67e8f9' }}>{ctx.t?.('panels.swarm-taskflow.types') ?? 'Types de tâches'} ({Object.keys(taskTypes).length})</div>
      {Object.entries(taskTypes).map(([type, info]: any) => (
        <div key={type} style={{ fontSize: 11, padding: '2px 0', color: '#94a3b8', borderBottom: '1px solid var(--mw-border, #1e293b)' }}>
          <span style={{ color: '#e2e8f0', fontWeight: 600 }}>{type}</span>
          {' '}← {(info.producers ?? []).join(', ') || '—'} | → {(info.consumers ?? []).join(', ') || '—'}
        </div>
      ))}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'swarm-taskflow',
  labelKey: 'panels.swarm-taskflow.titre',
  iconKey: 'panels.swarm-taskflow.titre',
  version: '0.1.0',
  essential: false,
  bundles: ['graphe'],
  paramsSchema: {},
  defaultParams: {},
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[swarm-taskflow] Taskflow de toutes les teams (teamflow + swarmflow)',
  component: SwarmTaskflowPanel,
};

export const langFr = LANG_FR;
