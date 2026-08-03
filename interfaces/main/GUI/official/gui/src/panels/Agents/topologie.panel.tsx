import React, { useEffect, useMemo, useState } from 'react';
import dagre from 'dagre';
import type { PanelDef } from '../../types.ts';
import { daemonPost } from '../../bridge.ts';

interface TopoNode {
  id: number;
  name: string;
  role_type: string;
  status: string;
  running: boolean;
  current_step: string | null;
  preemptible: boolean;
  priority: number;
  llm: string | null;
  successor_id: number | null;
}

interface TopoEdge {
  from: number | null;
  to: number;
  type: 'team_lead' | 'member' | 'successor';
  team?: string;
  topology?: string;
  leader_name?: string | null;
}

const STATUS_COLOR: Record<string, string> = {
  INIT: '#64748b', IDLE: '#0e7490', RUNNING: '#16a34a', STOPPED: '#dc2626', TERMINATED: '#7f1d1d',
};
const EDGE_COLOR: Record<string, string> = {
  team_lead: '#f59e0b', member: '#64748b', successor: '#a855f7',
};

const NODE_W = 170;
const NODE_H = 74;

function TopologyGraph() {
  const [data, setData] = useState<{ nodes: TopoNode[]; edges: TopoEdge[]; teams: string[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);

  const load = async () => {
    try {
      setError(null);
      const r = await daemonPost('agent/topology', {});
      const result = r?.result || r;
      if (result?.nodes) setData(result);
      else setError('Réponse inattendue');
    } catch (e: any) {
      setError(e?.message || 'Erreur de chargement');
    }
  };

  useEffect(() => { load(); }, []);

  const layout = useMemo(() => {
    if (!data) return null;
    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: 70, marginx: 20, marginy: 20 });
    const byId = new Map(data.nodes.map(n => [n.id, n]));
    for (const n of data.nodes) g.setNode(String(n.id), { width: NODE_W, height: NODE_H });
    for (const e of data.edges) {
      if (e.from === null || !byId.has(e.from) || !byId.has(e.to)) continue;
      g.setEdge(String(e.from), String(e.to));
    }
    dagre.layout(g);
    const pos = new Map<string, { x: number; y: number }>();
    for (const n of data.nodes) {
      const p = g.node(String(n.id));
      if (p) pos.set(String(n.id), { x: p.x, y: p.y });
    }
    const g2 = g.graph();
    return { pos, width: g2.width, height: g2.height };
  }, [data]);

  if (error) return <div style={{ color: '#fca5a5', fontSize: '0.8rem', padding: '1rem' }}>{error} <button onClick={load} style={{ marginLeft: '0.5rem', background: '#2563eb', color: 'white', border: 'none', borderRadius: '0.3rem', padding: '0.2rem 0.6rem', cursor: 'pointer' }}>↻</button></div>;
  if (!data || !layout) return <div style={{ color: '#94a3b8', fontSize: '0.8rem', padding: '1rem' }}>Chargement de la topologie…</div>;

  const byId = new Map(data.nodes.map(n => [n.id, n]));
  const shortName = (n: TopoNode) => {
    const m = n.name.match(/^team:[^/]+\/(.+)$/);
    return m ? m[1] : n.name;
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', flex: 1, minHeight: 0 }}>
      <div style={{ display: 'flex', gap: '0.8rem', fontSize: '0.7rem', color: '#94a3b8', flexWrap: 'wrap' }}>
        <span style={{ color: '#f59e0b' }}>━ leader d'équipe</span>
        <span style={{ color: '#64748b' }}>━ membre</span>
        <span style={{ color: '#a855f7' }}>━ successeur</span>
        <span style={{ marginLeft: 'auto' }}>
          <button onClick={load} style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: '0.3rem', padding: '0.2rem 0.7rem', fontSize: '0.7rem', cursor: 'pointer', marginRight: '0.4rem' }}>↻ Actualiser</button>
          <button onClick={() => setZoom(z => Math.max(0.4, z - 0.15))} style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: '0.3rem', padding: '0.2rem 0.5rem', fontSize: '0.7rem', cursor: 'pointer' }}>−</button>
          <button onClick={() => setZoom(1)} style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: '0.3rem', padding: '0.2rem 0.5rem', fontSize: '0.7rem', cursor: 'pointer', margin: '0 0.3rem' }}>{Math.round(zoom * 100)}%</button>
          <button onClick={() => setZoom(z => Math.min(2, z + 0.15))} style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: '0.3rem', padding: '0.2rem 0.5rem', fontSize: '0.7rem', cursor: 'pointer' }}>+</button>
        </span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', border: '1px solid #334155', borderRadius: '0.5rem', background: '#0f172a', position: 'relative' }}>
        <svg width={layout.width * zoom + 40} height={layout.height * zoom + 40} style={{ display: 'block' }}>
          <g transform={`translate(20,20) scale(${zoom})`}>
            {data.edges.map((e, i) => {
              if (e.from === null) return null;
              const a = byId.get(e.from), b = byId.get(e.to);
              if (!a || !b) return null;
              const pa = layout.pos.get(String(a.id)), pb = layout.pos.get(String(b.id));
              if (!pa || !pb) return null;
              const x1 = pa.x, y1 = pa.y + NODE_H / 2;
              const x2 = pb.x, y2 = pb.y - NODE_H / 2;
              const c = EDGE_COLOR[e.type] || '#64748b';
              const dash = e.type === 'successor' ? '6 4' : undefined;
              return (
                <g key={i}>
                  <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={c} strokeWidth={e.type === 'team_lead' ? 2.2 : 1.4} strokeDasharray={dash} opacity={0.85} />
                  <circle cx={x2} cy={y2} r={3.5} fill={c} />
                </g>
              );
            })}
            {data.edges.map((e, i) => {
              if (e.from !== null || !byId.has(e.to)) return null;
              const b = byId.get(e.to);
              const pb = layout.pos.get(String(b!.id));
              if (!pb) return null;
              return (
                <g key={`lead-${i}`}>
                  <rect x={pb.x - 8} y={pb.y - NODE_H / 2 - 16} width={16} height={16} rx={4} fill="#f59e0b" />
                  <text x={pb.x} y={pb.y - NODE_H / 2 - 4} textAnchor="middle" fontSize="9" fill="#0f172a" fontWeight="700">★</text>
                  {e.team ? (
                    <text x={pb.x} y={pb.y - NODE_H / 2 - 24} textAnchor="middle" fontSize="8" fill="#fbbf24">{e.team}{e.topology ? ` (${e.topology})` : ''}</text>
                  ) : null}
                </g>
              );
            })}
            {data.nodes.map(n => {
              const p = layout.pos.get(String(n.id));
              if (!p) return null;
              const x = p.x - NODE_W / 2, y = p.y - NODE_H / 2;
              const color = STATUS_COLOR[n.status] || '#64748b';
              return (
                <g key={n.id}>
                  <rect x={x} y={y} width={NODE_W} height={NODE_H} rx={8} fill="#1e293b" stroke={color} strokeWidth={n.running ? 2 : 1.2} />
                  <circle cx={x + 10} cy={y + 12} r={4} fill={color} />
                  {n.preemptible && <text x={x + NODE_W - 10} y={y + 14} textAnchor="middle" fontSize="8" fill="#fbbf24">⚡</text>}
                  <text x={x + NODE_W / 2} y={y + 30} textAnchor="middle" fontSize="10.5" fontWeight="600" fill="#e2e8f0">{shortName(n)}</text>
                  <text x={x + NODE_W / 2} y={y + 44} textAnchor="middle" fontSize="8.5" fill="#94a3b8">{n.role_type}{n.llm ? ` · ${n.llm}` : ''}</text>
                  <text x={x + NODE_W / 2} y={y + 58} textAnchor="middle" fontSize="8" fill={color}>{n.status}{n.running ? (n.current_step ? ` · 🪜 ${n.current_step}` : ' · ●') : ''}</text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      <div style={{ fontSize: '0.68rem', color: '#64748b' }}>
        {data.nodes.length} agents · {data.teams.length} équipes · ⚡ = préemptible
      </div>
    </div>
  );
}

export const Panel: PanelDef = {
  id: "agents-topologie", label: "Topologie", icon: "topologie", version: "1.0.0",
  description: "Graphe des relations entre agents (équipes, leader, membres, successeurs)",
  daemonRoutes: [
    { route: "agent/topology", methods: ["GET"], desc: "Graphe de topologie (nœuds + arêtes)" },
  ],
  declaration: () => "[agents-topologie] Topologie v1.0.0\n  Route: agent/topology\n  Relations : team_lead, member, successor",
  component: ({ ctx }) => React.createElement(TopologyGraph),
};
