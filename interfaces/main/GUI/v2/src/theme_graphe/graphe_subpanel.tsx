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
import {
  ReactFlow, Background, Controls, useNodesState, useEdgesState,
  Handle, Position, type Node, type Edge, type NodeProps,
} from '@xyflow/react';
import dagre from 'dagre';
import '@xyflow/react/dist/style.css';
import { GraphDoc, parseGraph, nodePosOf, setNodePos, stripPositions } from './grapheTypes.ts';
import { ThemeGraphe, loadTheme, nodeStyleOf, edgeStyleOf } from './themeGraphe.ts';
import {
  computeEdgePorts, toBoxes, anchorPoint,
  type Side, type NodeBox,
} from './graphLayout.ts';
import {
  layoutByAlgo, LAYOUT_ALGOS, LAYOUT_DIRS,
  type LayoutAlgo, type LayoutDir,
} from './graphLayouts.ts';
import { buildExpandedGraph } from './graphExpand.ts';

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

// ── Layout (multi-algos × directions) ───────────────────────────────
function layoutNodes(
  nodes: GraphDoc['nodes'],
  edges: GraphDoc['edges'],
  algo: LayoutAlgo = 'dagre',
  dir: LayoutDir = 'LR',
): Map<string, { x: number; y: number }> {
  return layoutByAlgo(nodes, edges, algo, dir);
}

// ── Nœud custom : 4 handles positionnés sur les côtés ───────────────
// Chaque nœud expose les 4 côtés (N/S/E/W) ; les arêtes relient les handles
// qui pointent vers leur cible/source (layout par côtés).
function FlowNode({ data }: NodeProps & { data?: any }) {
  const n: any = data?.n;
  const st: any = data?.style;
  const hasInner = !!data?.hasInner;
  const expanded = !!data?.expanded;
  const onToggle = data?.onToggle;
  const hs = { width: 6, height: 6, background: '#64748b', border: '1px solid #0f172a' };

  // Déplié = container : header en haut (nom/tag + bouton fold), les sous-
  // nœuds (rendus par React Flow via parentId) occupent le reste en dessous.
  if (expanded) {
    return (
      <div style={{
        width: '100%', height: '100%', boxSizing: 'border-box',
        background: 'rgba(148,163,184,.06)',
        border: `1.5px dashed ${st?.border ?? '#64748b'}`,
        borderRadius: 6, position: 'relative', overflow: 'hidden',
      }}>
        {/* Header du container */}
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: HEADER_H,
          display: 'flex', alignItems: 'center', gap: 4, padding: '0 6px',
          background: st?.color ?? '#475569', color: '#0f172a',
          fontWeight: 700, fontSize: 11, borderBottom: `1px solid ${st?.border ?? '#64748b'}`,
          boxSizing: 'border-box',
        }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
            {`${st?.icon ?? ''} ${n?.label ?? ''}`}
          </span>
          {hasInner && (
            <button
              onClick={(ev) => { ev.stopPropagation(); onToggle?.(n.id); }}
              style={{
                width: 16, height: 16, lineHeight: '13px', padding: 0, fontSize: 12,
                background: '#0f172a', color: '#e2e8f0', border: '1px solid #475569',
                borderRadius: 3, cursor: 'pointer',
              }}
              title="Replier"
            >−</button>
          )}
        </div>
        {/* Les sous-nœuds parentId sont rendus par React Flow en dessous */}
      </div>
    );
  }

  return (
    <div style={{
      width: '100%', height: '100%',
      background: st?.color,
      border: `1.5px solid ${st?.border ?? '#64748b'}`,
      borderRadius: st?.shape === 'pill' ? 999 : (st?.shape === 'rounded' || st?.shape === 'circle') ? 8 : 3,
      color: '#0f172a', fontWeight: 600, fontSize: 11,
      display: 'flex', alignItems: 'center', justifyContent: 'center', boxSizing: 'border-box',
      position: 'relative',
    }}>
      <span style={{ lineHeight: 1.2, textAlign: 'center', padding: '0 18px' }}>
        {`${st?.icon ?? ''} ${n?.label ?? ''}`}
      </span>
      {/* Bouton de dépliage : + à droite */}
      {hasInner && (
        <button
          onClick={(ev) => { ev.stopPropagation(); onToggle?.(n.id); }}
          style={{
            position: 'absolute', right: 3, top: '50%', transform: 'translateY(-50%)',
            width: 16, height: 16, lineHeight: '13px', padding: 0, fontSize: 12,
            background: '#0f172a', color: '#e2e8f0', border: '1px solid #475569',
            borderRadius: 3, cursor: 'pointer',
          }}
          title="Déplier"
        >+</button>
      )}
      <Handle type="source" position={Position.Left} id="ws" style={hs} />
      <Handle type="target" position={Position.Left} id="wt" style={hs} />
      <Handle type="source" position={Position.Right} id="es" style={hs} />
      <Handle type="target" position={Position.Right} id="et" style={hs} />
      <Handle type="source" position={Position.Top} id="ns" style={hs} />
      <Handle type="target" position={Position.Top} id="nt" style={hs} />
      <Handle type="source" position={Position.Bottom} id="ss" style={hs} />
      <Handle type="target" position={Position.Bottom} id="st" style={hs} />
    </div>
  );
}

const HEADER_H = 22;
const nodeTypes = { flow: FlowNode };

// ── Moteur SVG/DOM maison (export, fallback) — layout par côtés ─────
function SvgRenderer({ g, theme, algo, dir }: { g: GraphDoc; theme: ThemeGraphe; algo: LayoutAlgo; dir: LayoutDir }) {
  const auto = layoutNodes(g.nodes, g.edges, algo, dir);
  const boxes = toBoxes(auto, () => [NODE_W, NODE_H] as [number, number],
    g.nodes.map((n) => n.id));
  const ports = computeEdgePorts(boxes, g.edges);
  const byKey = new Map<string, { s: Side; t: Side; sp: number; tp: number }>();
  for (const p of ports) byKey.set(`${p.from}|${p.to}`,
    { s: p.sourceSide, t: p.targetSide, sp: p.sourcePos, tp: p.targetPos });
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
        const ba = boxes.get(e.from); const bb = boxes.get(e.to);
        const pt = byKey.get(`${e.from}|${e.to}`);
        const st = edgeStyleOf(theme, e.type);
        // Point d'ancrage par côté (plus proche de la bordure).
        const [x1, y1] = ba && pt ? anchorPoint(ba, pt.s, pt.sp) : [a.x + NODE_W, a.y + NODE_H / 2];
        const [x2, y2] = bb && pt ? anchorPoint(bb, pt.t, pt.tp) : [b.x, b.y + NODE_H / 2];
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

  // État de dépliage : ensemble des ids de nœuds dépliés.
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());
  const toggle = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);

  // Layout algo/direction
  const [algo, setAlgo] = React.useState<LayoutAlgo>('dagre');
  const [dir, setDir] = React.useState<LayoutDir>('LR');

  // Construction du graphe étendu (déplié) à partir du doc + état.
  const { rfNodes, rfEdges } = useMemo(() => {
    const r = buildExpandedGraph(graph, { algo, dir, theme: parsedTheme, expanded, onToggle: toggle });
    return { rfNodes: r.nodes, rfEdges: r.edges };
  }, [graph, parsedTheme, algo, dir, expanded, toggle]);

  const [nodes, setNodes, onNodesChange] = useNodesState<any>(rfNodes as any[]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<any>(rfEdges as any[]);

  // Re-sync quand le graphe étendu change (doc, algo, dir, expanded).
  React.useEffect(() => {
    setNodes(rfNodes);
    setEdges(rfEdges);
  }, [rfNodes, rfEdges, setNodes, setEdges]);

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
    else setExpanded(new Set()); // replier tout
  }, [graph, onGraphChange]);

  // Collecte récursive des ids de nœuds dépliables (ayant vars.inner).
  const collectExpandable = useCallback((g: GraphDoc, acc: Set<string> = new Set()): Set<string> => {
    for (const n of g.nodes) {
      if (n.vars?.inner) acc.add(n.id);
    }
    return acc;
  }, []);

  const unfoldAll = useCallback(() => {
    const all = collectExpandable(graph);
    if (all.size) setExpanded(new Set(all));
  }, [graph, collectExpandable]);

  const foldAll = useCallback(() => {
    setExpanded(new Set());
  }, []);

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
      {/* Barre d'outils : algo × direction */}
      <div style={{ display: 'flex', gap: 6, padding: '2px 8px 6px', alignItems: 'center', flexWrap: 'wrap' }}>
        {/* Algortihmes */}
        <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
          {LAYOUT_ALGOS.map((a) => (
            <button key={a} className="mw-btn"
              onClick={() => setAlgo(a)}
              style={{
                fontSize: 10, padding: '1px 8px', textTransform: 'capitalize',
                opacity: algo === a ? 1 : 0.45,
                borderColor: algo === a ? '#38bdf8' : undefined,
                color: algo === a ? '#38bdf8' : undefined,
              }}>
              {a}
            </button>
          ))}
        </div>
        {/* Directions (flèches) */}
        <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
          {LAYOUT_DIRS.map((d) => (
            <button key={d} className="mw-btn" title={`direction ${d}`}
              onClick={() => setDir(d)}
              style={{
                fontSize: 10, padding: '1px 6px', lineHeight: 1,
                opacity: dir === d ? 1 : 0.45,
                borderColor: dir === d ? '#38bdf8' : undefined,
                color: dir === d ? '#38bdf8' : undefined,
              }}>
              {d === 'LR' ? '→' : d === 'RL' ? '←' : d === 'TB' ? '↓' : '↑'}
            </button>
          ))}
        </div>
        <span style={{ flex: 1 }} />
        <button className="mw-btn" style={{ fontSize: 10, padding: '1px 8px' }} onClick={unfoldAll} title="Tout déplier">
          ⊕ Tout déplier
        </button>
        <button className="mw-btn" style={{ fontSize: 10, padding: '1px 8px' }} onClick={foldAll} title="Tout replier">
          ⊖ Tout replier
        </button>
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
            nodeTypes={nodeTypes}
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
            <SvgRenderer g={graph} theme={parsedTheme} algo={algo} dir={dir} />
          </div>
        )}
        {/* Host SVG invisible pour export (mode React Flow) */}
        {useRF && (
          <div ref={svgHostRef} style={{ position: 'absolute', left: -99999, top: 0, width: 800 }}
            data-testid="graphe-svg-hidden" aria-hidden>
            <SvgRenderer g={graph} theme={parsedTheme} algo={algo} dir={dir} />
          </div>
        )}
      </div>
    </div>
  );
}

export default GrapheSubPanel;
