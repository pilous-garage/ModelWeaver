// graphExpand.ts — rendu HIÉRARCHIQUE du graphe (dépliage/fold des nœuds).
//
// Transforme un GraphDoc (avec vars.inner sur les nœuds dépliables) en un
// ensemble de nœuds/arêtes React Flow PLATS, selon l'état d'expansion :
//
//   - PLIÉ   : le nœud est seul (avec un indicateur de dépliable). SES ARÊTES
//              INTERNES NE SONT PAS AFFICHÉES. Les arêtes externes le relient.
//   - DÉPLIÉ : le nœud devient un CONTAINER (box underlay) qui englobe ses
//              sous-nœuds (parentId). Ses arêtes internes apparaissent, et les
//              arêtes externes sont RÉ-ROUTÉES : elles entrent par l'entrypoint
//              interne et sortent par les exitpoints internes.
//
// Layout : dagre sur le graphe TOP (parents). Chaque container déplié est un
// nœud dagre dont la taille englobe ses sous-nœuds (layout récursif local).

import dagre from 'dagre';
import type { Node as RFNode, Edge as RFEdge, Position } from '@xyflow/react';
import { nodeStyleOf, type ThemeGraphe } from './themeGraphe.ts';
import type { GraphDoc, GraphNode, GraphEdge } from './grapheTypes.ts';

const NODE_W = 150;
const NODE_H = 44;
const PAD = 40;   // padding intérieur du container (box underlay)
const BOX_W = 220;
const BOX_H = 60;

export interface ExpandOptions {
  algo?: 'dagre' | 'compact' | 'simplex';
  dir?: 'LR' | 'RL' | 'TB' | 'BT';
  theme: ThemeGraphe;
  expanded: Set<string>;
  onToggle: (id: string) => void;
}

interface LaidNode {
  id: string;
  x: number;  // top-left absolu
  y: number;
  w: number;
  h: number;
  kind: 'plain' | 'container';
}

// Layout dagre d'un ensemble de nœuds/arêtes (positions absolues).
function dagreLayout(
  ids: string[],
  edges: { from: string; to: string }[],
  algo: string, dir: string,
): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  const nodesep = algo === 'compact' ? 12 : 30;
  const ranksep = algo === 'compact' ? 28 : 60;
  const ranker = algo === 'compact' ? 'tight-tree' : algo === 'simplex' ? 'network-simplex' : undefined;
  g.setGraph({ rankdir: dir, nodesep, ranksep, ...(ranker ? { ranker } : {}) });
  for (const id of ids) g.setNode(id, { width: NODE_W, height: NODE_H });
  for (const e of edges) if (e.from && e.to) g.setEdge(e.from, e.to);
  dagre.layout(g);
  const pos = new Map<string, { x: number; y: number }>();
  for (const id of ids) {
    const p = g.node(id) as { x?: number; y?: number } | undefined;
    pos.set(id, { x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - NODE_H / 2 });
  }
  return pos;
}

// Layout RÉCURSIF : pour un nœud (id, type) et son inner, place les
// sous-nœuds DANS une box relative, retourne { laid, size }.
interface InnerLayout {
  childPos: Map<string, { x: number; y: number }>; // positions RELATIVES à la box
  boxW: number;
  boxH: number;
}

function layoutInner(
  inner: { nodes: GraphNode[]; edges: GraphEdge[] },
  algo: string, dir: string,
): InnerLayout {
  const ids = inner.nodes.map((n) => n.id);
  const es = inner.edges.map((e) => ({ from: e.from, to: e.to }));
  const pos = dagreLayout(ids, es, algo, dir);
  // Bounding box des enfants.
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const id of ids) {
    const p = pos.get(id);
    if (!p) continue;
    minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + NODE_W); maxY = Math.max(maxY, p.y + NODE_H);
  }
  if (!isFinite(minX)) { minX = 0; minY = 0; maxX = BOX_W; maxY = BOX_H; }
  const boxW = maxX - minX + PAD * 2;
  const boxH = maxY - minY + PAD * 2;
  const childPos = new Map<string, { x: number; y: number }>();
  for (const id of ids) {
    const p = pos.get(id);
    if (!p) continue;
    // Relatif au coin de la box (le container).
    childPos.set(id, { x: p.x - minX + PAD, y: p.y - minY + PAD });
  }
  return { childPos, boxW, boxH };
}

// Calcule la liste "à plat" des nœuds + positions absolues, en remontant les
// containers. Retourne les nœuds RF (avec parentId) et la position du TOP.
export function buildExpandedGraph(
  graph: GraphDoc,
  opts: ExpandOptions,
): { nodes: RFNode[]; edges: RFEdge[] } {
  const algo = opts.algo ?? 'dagre';
  const dir = opts.dir ?? 'LR';
  const theme = opts.theme;
  const expanded = opts.expanded;

  const rfNodes: RFNode[] = [];
  const rfEdges: RFEdge[] = [];
  const topIds: string[] = [];
  const topEdges: { from: string; to: string }[] = [];

  // 1) Layout du graphe TOP (les nœuds racines du doc, avec leurs arêtes).
  const innerCache = new Map<string, InnerLayout>();
  for (const n of graph.nodes) {
    topIds.push(n.id);
    if (expanded.has(n.id) && n.vars?.inner) {
      innerCache.set(n.id, layoutInner(n.vars.inner, algo, dir));
    }
  }
  for (const e of graph.edges) topEdges.push({ from: e.from, to: e.to });
  const topPos = dagreLayout(topIds, topEdges, algo, dir);

  // 2) Pour chaque nœud TOP, créer le nœud RF (ou container déplié).
  for (const n of graph.nodes) {
    const st = nodeStyleOf(theme, n.type);
    const pos = topPos.get(n.id) || { x: 0, y: 0 };
    const isExpanded = expanded.has(n.id) && n.vars?.inner;
    const inner = isExpanded ? innerCache.get(n.id) : null;

    const size: [number, number] = inner ? [inner.boxW, inner.boxH] : [NODE_W, NODE_H];
    const data: any = {
      label: `${st.icon ?? ''} ${n.label}`,
      n,
      style: st,
      hasInner: !!n.vars?.inner,
      expanded: !!isExpanded,
      onToggle: opts.onToggle,
    };
    rfNodes.push({
      id: n.id,
      type: 'flow',
      position: { x: pos.x, y: pos.y },
      data,
      style: { width: size[0], height: size[1] },
    } as RFNode);

    // 3) Sous-nœuds (si déplié) : parentId + positions relatives.
    if (inner) {
      const innerNodes = n.vars!.inner.nodes;
      for (const sub of innerNodes) {
        const subSt = nodeStyleOf(theme, sub.type);
        const rel = inner.childPos.get(sub.id) || { x: 0, y: 0 };
        rfNodes.push({
          id: sub.id,
          type: 'flow',
          parentId: n.id,
          position: { x: rel.x, y: rel.y },
          data: {
            label: `${subSt.icon ?? ''} ${sub.label}`,
            n: sub,
            style: subSt,
            hasInner: !!sub.vars?.inner,
            expanded: expanded.has(sub.id) && !!sub.vars?.inner,
            onToggle: opts.onToggle,
          },
          style: { width: NODE_W, height: NODE_H },
        } as RFNode);
      }
      // Arêtes internes (seulement si déplié).
      for (const ie of n.vars!.inner.edges) {
        rfEdges.push(makeEdge(ie, theme, ie.type === 'loop' ? 'loop' : 'next'));
      }
    }

    // 4) Arêtes externes : si le parent est déplié, l'arête qui entre pointe
    //    vers l'entrypoint interne ; celle qui sort part des exitpoints.
    const innerEntry = isExpanded ? n.vars!.inner.entrypoint : null;
    const innerExits = isExpanded ? (n.vars!.inner.exitpoints ?? []) : [];
  }

  // 5) Arêtes TOP (externes).
  for (const e of graph.edges) {
    const src = graph.nodes.find((n) => n.id === e.from);
    const tgt = graph.nodes.find((n) => n.id === e.to);
    const srcExp = expanded.has(e.from) && !!src?.vars?.inner;
    const tgtExp = expanded.has(e.to) && !!tgt?.vars?.inner;
    const from = srcExp ? (tgt?.vars?.inner?.entrypoint ?? e.from) : e.from;
    const to = tgtExp ? (tgt?.vars?.inner?.entrypoint ?? e.to) : e.to;
    rfEdges.push(makeEdge({ from, to, label: e.label, type: e.type }, theme, e.type));
  }

  return { nodes: rfNodes, edges: rfEdges };
}

function makeEdge(e: GraphEdge, theme: ThemeGraphe, type?: string): RFEdge {
  const st = (theme.edges as any)[type || 'next'] || (theme.edges as any).next || {};
  return {
    id: `${e.from}->${e.to}`,
    source: e.from,
    target: e.to,
    label: e.label,
    style: {
      stroke: st.color || '#94a3b8',
      strokeDasharray: st.style === 'dashed' ? '5 4' : st.style === 'dotted' ? '2 3' : undefined,
    },
    markerEnd: st.arrow ? { type: 'arrowclosed', color: st.color || '#94a3b8' } : undefined,
  } as RFEdge;
}
