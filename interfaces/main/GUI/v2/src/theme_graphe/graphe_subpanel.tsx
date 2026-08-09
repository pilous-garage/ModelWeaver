// graphe_subpanel.tsx — module générique de rendu de graphe.
// Affiche un graphe (nodes + edges, format graph.yaml) avec un thème
// (theme.yaml) de façon cohérente et répétitive.
//
// Deux moteurs de rendu :
//   - React Flow (@xyflow/react) : interactif (drag, zoom, édition live des pos)
//   - SVG/DOM maison           : export propre (svg sérialisable, analyse IA,
//                                fallback si deps cassées)
//
// Positions OPTIONNELLES : node.vars.pos = {center, orientation, size}.
// Absent → layout auto (dagre). Présent → position fixe.
// `editable` : false = lecture seule ; true = drag met à jour les pos.

import React, { useMemo, useCallback } from 'react';
import { parse as yamlParse } from 'yaml';
import { ReactFlow, Background, Controls, useNodesState, useEdgesState, type Node, type Edge } from '@xyflow/react';
import dagre from 'dagre';
import '@xyflow/react/dist/style.css';
import { GraphDoc, parseGraph, nodePosOf, setNodePos, stripPositions } from './grapheTypes.ts';
import { ThemeGraphe, loadTheme, nodeStyleOf, edgeStyleOf } from './themeGraphe.ts';

export interface GrapheSubPanelProps {
  doc: string | Record<string, any> | null;   // graph.yaml (string) ou objet
  theme?: string | Record<string, any> | null; // theme.yaml (string) ou objet
  editable?: boolean;
  engine?: 'auto' | 'reactflow' | 'svg';       // auto = reactflow, fallback svg si erreur
  onGraphChange?: (g: GraphDoc) => void;       // appelé à chaque édition (pos)
  onExportSvg?: (svg: string) => void;
  height?: number | string;
}

const NODE_W = 150;
const NODE_H = 44;

// ── Layout dagre (positions auto) ──────────────────────────────────
function layoutNodes(nodes: GraphDoc['nodes'], edges: GraphDoc['edges']): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'LR', nodesep: 30, ranksep: 60 });
  for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  for (const e of edges) g.setEdge(e.from, e.to);
  dagre.layout(g);
  const pos = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    const p = g.node(n.id) as { x: number; y: number } | undefined;
    pos.set(n.id, { x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - NODE_H / 2 });
  }
  return pos;
}

// ── Conversion GraphDoc → React Flow nodes/edges ────────────────────
function toFlowNodes(g: GraphDoc, theme: ThemeGraphe): Node[] {
  const auto = layoutNodes(g.nodes, g.edges);
  return g.nodes.map((n: any) => {
    const st = nodeStyleOf(theme, n.type);
    const pos = nodePosOf(n);
    const xy = pos?.center
      ? { x: pos.center[0] - (pos.size?.[0] ?? NODE_W) / 2, y: pos.center[1] - (pos.size?.[1] ?? NODE_H) / 2 }
      : auto.get(n.id) || { x: 0, y: 0 };
    const size = pos?.size ?? [NODE_W, NODE_H];
    return {
      id: n.id,
      position: xy,
      data: { label: `${st.icon ?? ''} ${n.label}`, n, style: st },
      style: {
        width: size[0], height: size[1],
        background: st.color, border: `1.5px solid ${st.border}`,
        borderRadius: st.shape === 'pill' ? 999 : (st.shape === 'rounded' || st.shape === 'circle') ? 8 : 3,
        color: '#0f172a', fontWeight: 600, fontSize: 11,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      },
    } as Node;
  });
}

function toFlowEdges(g: GraphDoc, theme: ThemeGraphe): Edge[] {
  return g.edges.map((e: any, i: number) => {
    const st = edgeStyleOf(theme, e.type);
    const labelIn = e.vars?.label_in;
    const labelOut = e.vars?.label_out;
    return {
      id: `${e.from}->${e.to}-${i}`,
      source: e.from,
      target: e.to,
      label: e.label || (labelIn && labelOut ? `${labelIn} → ${labelOut}` : undefined),
      animated: e.type === 'token',
      style: { stroke: st.color, strokeDasharray: st.style === 'dashed' ? '5 4' : st.style === 'dotted' ? '2 3' : undefined },
      markerEnd: st.arrow ? { type: 'arrowclosed', color: st.color } : undefined,
    } as Edge;
  });
}

// ── Moteur SVG/DOM maison (export, fallback) ────────────────────────
function SvgRenderer({ g, theme }: { g: GraphDoc; theme: ThemeGraphe }) {
  const auto = layoutNodes(g.nodes, g.edges);
  const width = 800;
  const height = Math.max(300, g.nodes.length * 70 + 40);
  return (
    <svg width="100%" viewBox={`0 0 ${width} ${height}`} style={{ background: '#0f172a' }} data-testid="graphe-svg">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b" />
        </marker>
      </defs>
      {/* Arêtes */}
      {g.edges.map((e: any, i: number) => {
        const a = auto.get(e.from); const b = auto.get(e.to);
        if (!a || !b) return null;
        const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2;
        const x2 = b.x, y2 = b.y + NODE_H / 2;
        const st = edgeStyleOf(theme, e.type);
        return (
          <g key={i}>
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={st.color}
              strokeWidth={1.5} strokeDasharray={st.style === 'dashed' ? '5 4' : st.style === 'dotted' ? '2 3' : undefined}
              markerEnd={st.arrow ? 'url(#arrow)' : undefined} />
            {e.label && (
              <text x={(x1 + x2) / 2} y={y1 - 4} fill="#94a3b8" fontSize={10} textAnchor="middle">{e.label}</text>
            )}
          </g>
        );
      })}
      {/* Nœuds */}
      {g.nodes.map((n: any) => {
        const p = auto.get(n.id); if (!p) return null;
        const st = nodeStyleOf(theme, n.type);
        const x = p.x, y = p.y;
        return (
          <g key={n.id}>
            <rect x={x} y={y} width={NODE_W} height={NODE_H} rx={st.shape === 'pill' ? NODE_H / 2 : 6}
              fill={st.color} stroke={st.border} strokeWidth={st.thick ? 2 : 1.5} />
            <text x={x + NODE_W / 2} y={y + NODE_H / 2 + 4} fill="#0f172a" fontSize={11}
              fontWeight={600} textAnchor="middle">{st.icon ? `${st.icon} ` : ''}{n.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

// ── Composant principal ─────────────────────────────────────────────
export function GrapheSubPanel(props: GrapheSubPanelProps) {
  const { doc, theme, editable = false, engine = 'auto', onGraphChange, height = '100%' } = props;
  const svgHostRef = React.useRef<HTMLDivElement>(null);

  const parsedTheme = useMemo(() => {
    if (!theme) return loadTheme(null);
    if (typeof theme === 'string') {
      try { return loadTheme(yamlParse(theme)); } catch { return loadTheme(null); }
    }
    return loadTheme(theme);
  }, [theme]);

  const graph: GraphDoc = useMemo(() => {
    if (!doc) return { nodes: [], edges: [] };
    try { return parseGraph(doc); } catch { return { nodes: [], edges: [] }; }
  }, [doc]);

  // React Flow state (positions éditables)
  const [nodes, setNodes, onNodesChange] = useNodesState<any>(toFlowNodes(graph, parsedTheme) as any[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>(toFlowEdges(graph, parsedTheme) as any[]);

  // Re-sync quand le doc change
  React.useEffect(() => {
    setNodes(toFlowNodes(graph, parsedTheme));
    setEdges(toFlowEdges(graph, parsedTheme));
  }, [graph, parsedTheme, setNodes, setEdges]);

  const onNodeDragStop = useCallback((_: any, node: Node) => {
    if (!editable || !onGraphChange) return;
    const g2 = setNodePos(graph, node.id, {
      center: [node.position.x + NODE_W / 2, node.position.y + NODE_H / 2],
    });
    onGraphChange(g2);
  }, [editable, graph, onGraphChange]);

  const reLayout = useCallback(() => {
    const stripped = stripPositions(graph);
    if (onGraphChange) onGraphChange(stripped);
    else setNodes(toFlowNodes(stripped, parsedTheme));
  }, [graph, onGraphChange, parsedTheme, setNodes]);

  if (!doc) return <div style={{ color: '#475569', padding: 8 }}>Aucun graphe</div>;

  // Moteur SVG (export/fallback) — le host est TOUJOURS rendu (invisible en
  // mode React Flow) pour que l'export capture le SVG réel.
  const useRF = engine === 'reactflow' || (engine === 'auto' && typeof ReactFlow === 'function');

  const doExportSvg = () => {
    if (!props.onExportSvg) return;
    const host = svgHostRef.current;
    if (host) props.onExportSvg(host.innerHTML);
  };

  return (
    <div style={{ height, display: 'flex', flexDirection: 'column', boxSizing: 'border-box' }}>
      {graph.title && (
        <div style={{ fontSize: 12, fontWeight: 700, color: '#a5b4fc', padding: '4px 8px' }}>{graph.title}</div>
      )}
      {/* Barre d'outils */}
      <div style={{ display: 'flex', gap: 6, padding: '2px 8px 6px', alignItems: 'center' }}>
        <button className="mw-btn" style={{ fontSize: 10, padding: '1px 8px' }} onClick={reLayout}>
          ⟳ Re-layout
        </button>
        {props.onExportSvg && (
          <button className="mw-btn" style={{ fontSize: 10, padding: '1px 8px' }} onClick={doExportSvg}>
            ⬇ SVG
          </button>
        )}
      </div>
      <div style={{ flex: 1, minHeight: 0, border: '1px solid var(--mw-border, #1e293b)', borderRadius: 6, overflow: 'hidden', position: 'relative' }}>
        {useRF ? (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeDragStop={onNodeDragStop}
            nodesDraggable={editable}
            fitView
            minZoom={0.2}
          >
            <Background gap={20} color="#1e293b" />
            <Controls />
          </ReactFlow>
        ) : (
          <div ref={svgHostRef} data-testid="graphe-svg-host">
            <SvgRenderer g={graph} theme={parsedTheme} />
          </div>
        )}
        {/* Host SVG invisible pour export (mode React Flow) */}
        {useRF && (
          <div ref={svgHostRef} style={{ position: 'absolute', left: -99999, top: 0, width: 800 }}
            data-testid="graphe-svg-hidden" aria-hidden>
            <SvgRenderer g={graph} theme={parsedTheme} />
          </div>
        )}
      </div>
    </div>
  );
}

export default GrapheSubPanel;
