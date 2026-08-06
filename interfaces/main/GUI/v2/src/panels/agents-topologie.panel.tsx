// agents/topologie — graphe des agents (nœuds + arêtes). Migré de V1.
// Route : agent/topology (poll 5s). Rendu simple (liste) — pas de dagre.

import React from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';

const LANG_FR = `
panels:
  agents-topologie:
    titre: "Topologie"
    noeuds: "Nœuds"
    aretes: "Arêtes"
    statut: "Statut"
    role: "Rôle"
    erreur: "Erreur"
`;

const LANG_EN = `
panels:
  agents-topologie:
    titre: "Topologie"
    noeuds: "Nœuds"
    aretes: "Arêtes"
    statut: "Status"
    role: "Rôle"
    erreur: "Error"
`;


function TopologiePanel({ ctx }: { ctx: any; params: Record<string, any> }) {
  const { data, error } = usePoll<any>(
    ctx.api.post, 'agent/topology', {}, 5000,
    (res) => res?.result ?? {}, true,
  );
  const nodes = data?.nodes ?? [];
  const edges = data?.edges ?? [];

  const byId = new Map<string, any>(nodes.map((n: any) => [n.id, n]));
  const edgeKey = (e: any) => `${e.from}→${e.to}`;

  return (
    <div className="mw-panel" style={{ height: '100%', overflow: 'auto', padding: 8, boxSizing: 'border-box', fontSize: 12 }}>
      {error && <div style={{ color: '#f87171', marginBottom: 6 }}>{ctx.t?.('panels.agents-topologie.erreur') ?? 'Erreur'} : {error}</div>}
      <div style={{ fontWeight: 600, margin: '4px 0' }}>{ctx.t?.('panels.agents-topologie.noeuds') ?? 'Nœuds'} ({nodes.length})</div>
      {nodes.map((n: any) => (
        <div key={n.id} style={{ display: 'flex', gap: 8, padding: '3px 0', borderBottom: '1px solid var(--mw-border, #1e293b)', alignItems: 'center' }}>
          <span style={{ flex: 1, fontWeight: 600 }}>{n.name}</span>
          <span style={{ width: 90, color: '#94a3b8' }}>{n.role_type ?? ''}</span>
          <span style={{ color: n.running ? '#4ade80' : '#64748b' }}>{n.running ? '●' : n.status}</span>
        </div>
      ))}
      {edges.length > 0 && (
        <>
          <div style={{ fontWeight: 600, margin: '8px 0 4px' }}>{ctx.t?.('panels.agents-topologie.aretes') ?? 'Arêtes'} ({edges.length})</div>
          {edges.map((e: any, i: number) => (
            <div key={edgeKey(e) + i} style={{ color: '#64748b', padding: '1px 0', fontSize: 11 }}>
              {byId.get(e.from)?.name ?? e.from} → {byId.get(e.to)?.name ?? e.to} <span style={{ color: '#475569' }}>({e.type ?? ''})</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'agents-topologie',
  labelKey: 'panels.agents-topologie.titre',
  iconKey: 'panels.agents-topologie.titre',
  version: '1.0.0',
  essential: false,
  bundles: ['agents'],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[agents-topologie] Topologie v1.0.0\n  routes: agent/topology',
  component: TopologiePanel,
};

export const langFr = LANG_FR;