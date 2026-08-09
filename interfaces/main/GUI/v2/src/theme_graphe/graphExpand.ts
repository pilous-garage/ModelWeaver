// graphExpand.ts — rendu HIÉRARCHIQUE récursif du graphe (dépliage/fold).
//
// Transforme un GraphDoc (avec vars.inner) en nœuds/arêtes React Flow PLATS,
// selon l'état d'expansion :
//   - PLIÉ   : le nœud est seul ; ses arêtes internes ne sont PAS affichées.
//   - DÉPLIÉ : container (box underlay) englobant ses sous-nœuds (parentId),
//              récursivement. Arêtes internes visibles ; arêtes externes
//              entrent par l'entrypoint et sortent par les exitpoints.
//
// Layout : PRÉCALCUL bottom-up des tailles (feuilles → racine), puis dagre par
// niveau. Chaque container déplié a une taille = header + bbox de SES enfants
// (eux-mêmes dépliés), connue avant le layout du parent.

import dagre from 'dagre';
import type { Node as RFNode, Edge as RFEdge } from '@xyflow/react';
import { nodeStyleOf, type ThemeGraphe } from './themeGraphe.ts';
import type { GraphDoc, GraphNode, GraphEdge } from './grapheTypes.ts';

const NODE_W = 150;
const NODE_H = 44;
const PAD = 40;
const HEADER_H = 22;
const BOX_W = 220;
const BOX_H = 60;

export interface ExpandOptions {
  algo?: 'dagre' | 'compact' | 'simplex';
  dir?: 'LR' | 'RL' | 'TB' | 'BT';
  theme: ThemeGraphe;
  expanded: Set<string>;
  onToggle: (id: string) => void;
}

interface Sizes {
  w: number; // taille du nœud (container déplié = box englobant ses enfants)
  h: number;
  childPos: Map<string, { x: number; y: number }> | null; // si déplié (relatif, sous header)
}

// ── PRÉCALCUL BOTTOM-UP des tailles (mémoïsé) ──────────────────────
// Calcule récursivement la taille de chaque nœud. Les feuilles d'abord,
// puis les containers dépliés (header + bbox de leurs enfants).
//
// MÉMOÏSATION incrémentale : le cache garde les tailles déjà calculées par
// nœud. Quand un nœud change d'état (fold/unfold), on invalide SA taille et
// on remonte récursivement vers les parents (qui dépendent de la taille de
// leurs enfants). Les sous-graphes non touchés sont réutilisés tels quels.

export interface SizeCache {
  map: Map<string, Sizes>;
  /** Calcule (mémoïsé, bottom-up) la taille d'un nœud. */
  compute(node: GraphNode, expanded: Set<string>, algo: string, dir: string): Sizes;
  /** Invalide le cache d'un nœud + tous ses ascendants (recalcul bottom-up). */
  invalidate(node: GraphNode): void;
}

export function createSizeCache(): SizeCache {
  const map = new Map<string, Sizes>();
  const key = (node: GraphNode, algo: string, dir: string) =>
    `${node.id}|${algo}|${dir}|${node.vars?.inner ? (node.vars.inner as any).nodes?.length ?? 0 : 0}`;

  function compute(node: GraphNode, expanded: Set<string>, algo: string, dir: string): Sizes {
    const k = key(node, algo, dir);
    const cached = map.get(k);
    // Un nœud plié est toujours NODE_W×NODE_H (indépendant de expanded) →
    // on ne cache que les résultats avec le bon état d'expansion (via k).
    if (cached) return cached;
    let s: Sizes;
    if (!node.vars?.inner || !expanded.has(node.id)) {
      s = { w: NODE_W, h: NODE_H, childPos: null };
    } else {
      const inner = node.vars.inner;
      const children: { id: string; s: Sizes }[] = [];
      for (const sub of inner.nodes) {
        children.push({ id: sub.id, s: compute(sub, expanded, algo, dir) });
      }
      const ids = children.map((c) => c.id);
      const es = (inner.edges ?? []).map((e: GraphEdge) => ({ from: e.from, to: e.to }));
      const sizeOf = (id: string) => {
        const c = children.find((x) => x.id === id);
        return { w: c?.s.w ?? NODE_W, h: c?.s.h ?? NODE_H };
      };
      const pos = dagreLayout(ids, es, algo, dir, sizeOf);
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const c of children) {
        const p = pos.get(c.id);
        if (!p) continue;
        minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
        maxX = Math.max(maxX, p.x + c.s.w); maxY = Math.max(maxY, p.y + c.s.h);
      }
      if (!isFinite(minX)) { minX = 0; minY = 0; maxX = BOX_W; maxY = BOX_H; }
      const w = maxX - minX + PAD * 2;
      const h = HEADER_H + (maxY - minY) + PAD * 2;
      const childPos = new Map<string, { x: number; y: number }>();
      for (const c of children) {
        const p = pos.get(c.id);
        if (!p) continue;
        childPos.set(c.id, { x: p.x - minX + PAD, y: p.y - minY + PAD + HEADER_H });
      }
      s = { w, h, childPos };
    }
    map.set(k, s);
    return s;
  }

  return {
    map,
    compute,
    invalidate(node: GraphNode) {
      // Retire les clés de CE nœud (les parents seront recalculés à la
      // demande dans compute, remontant automatiquement).
      for (const k of [...map.keys()]) {
        if (k.startsWith(node.id + '|')) map.delete(k);
      }
    },
  };
}

function computeSize(node: GraphNode, expanded: Set<string>, algo: string, dir: string): Sizes {
  if (!node.vars?.inner || !expanded.has(node.id)) {
    return { w: NODE_W, h: NODE_H, childPos: null };
  }
  const inner = node.vars.inner;
  const children: { id: string; s: Sizes }[] = [];
  for (const sub of inner.nodes) {
    children.push({ id: sub.id, s: computeSize(sub, expanded, algo, dir) });
  }
  // Layout dagre du sous-graphe avec les tailles réelles des enfants.
  const ids = children.map((c) => c.id);
  const es = (inner.edges ?? []).map((e: GraphEdge) => ({ from: e.from, to: e.to }));
  const sizeOf = (id: string) => {
    const c = children.find((x) => x.id === id);
    return { w: c?.s.w ?? NODE_W, h: c?.s.h ?? NODE_H };
  };
  const pos = dagreLayout(ids, es, algo, dir, sizeOf);
  // Bounding box des enfants (vraies tailles).
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const c of children) {
    const p = pos.get(c.id);
    if (!p) continue;
    minX = Math.min(minX, p.x); minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x + c.s.w); maxY = Math.max(maxY, p.y + c.s.h);
  }
  if (!isFinite(minX)) { minX = 0; minY = 0; maxX = BOX_W; maxY = BOX_H; }
  const w = maxX - minX + PAD * 2;
  const h = HEADER_H + (maxY - minY) + PAD * 2;
  const childPos = new Map<string, { x: number; y: number }>();
  for (const c of children) {
    const p = pos.get(c.id);
    if (!p) continue;
    childPos.set(c.id, { x: p.x - minX + PAD, y: p.y - minY + PAD + HEADER_H });
  }
  return { w, h, childPos };
}

// ── Layout dagre (positions absolues) ───────────────────────────────

function dagreLayout(
  ids: string[],
  edges: { from: string; to: string }[],
  algo: string, dir: string,
  sizeOf: (id: string) => { w: number; h: number } = () => ({ w: NODE_W, h: NODE_H }),
): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  const nodesep = algo === 'compact' ? 12 : 30;
  const ranksep = algo === 'compact' ? 28 : 60;
  const ranker = algo === 'compact' ? 'tight-tree' : algo === 'simplex' ? 'network-simplex' : undefined;
  g.setGraph({ rankdir: dir, nodesep, ranksep, ...(ranker ? { ranker } : {}) });
  for (const id of ids) {
    const sz = sizeOf(id);
    g.setNode(id, { width: sz.w, height: sz.h });
  }
  for (const e of edges) if (e.from && e.to) g.setEdge(e.from, e.to);
  dagre.layout(g);
  const pos = new Map<string, { x: number; y: number }>();
  for (const id of ids) {
    const p = g.node(id) as { x?: number; y?: number } | undefined;
    const sz = sizeOf(id);
    pos.set(id, { x: (p?.x ?? 0) - sz.w / 2, y: (p?.y ?? 0) - sz.h / 2 });
  }
  return pos;
}

// ── Ajout récursif des nœuds (avec parentId) ───────────────────────

function addNodeRecursive(
  node: GraphNode,
  rfNodes: RFNode[],
  rfEdges: RFEdge[],
  theme: ThemeGraphe,
  opts: ExpandOptions,
  absPos: { x: number; y: number },
  size: { w: number; h: number },
  parentId?: string,
  cache?: SizeCache,
): void {
  const st = nodeStyleOf(theme, node.type);
  const isExpanded = opts.expanded.has(node.id) && !!node.vars?.inner;
  const data: any = {
    label: `${st.icon ?? ''} ${node.label}`,
    n: node, style: st,
    hasInner: !!node.vars?.inner,
    expanded: isExpanded,
    onToggle: opts.onToggle,
  };
  const rf: any = {
    id: node.id,
    type: 'flow',
    position: { x: absPos.x, y: absPos.y },
    data,
    style: { width: size.w, height: size.h },
  };
  if (parentId) rf.parentId = parentId;
  rfNodes.push(rf);

  // Sous-nœuds du container (si déplié) — positions relatives depuis le cache.
  if (isExpanded && node.vars?.inner) {
    const inner = node.vars.inner;
    const sizes = cache
      ? cache.compute(node, opts.expanded, opts.algo ?? 'dagre', opts.dir ?? 'LR')
      : computeSize(node, opts.expanded, opts.algo ?? 'dagre', opts.dir ?? 'LR');
    const childPos = sizes.childPos!;
    for (const sub of inner.nodes) {
      const rel = childPos.get(sub.id) || { x: 0, y: 0 };
      const subSizes = cache
        ? cache.compute(sub, opts.expanded, opts.algo ?? 'dagre', opts.dir ?? 'LR')
        : computeSize(sub, opts.expanded, opts.algo ?? 'dagre', opts.dir ?? 'LR');
      addNodeRecursive(sub, rfNodes, rfEdges, theme, opts, rel, subSizes, node.id, cache);
    }
    for (const ie of inner.edges) {
      rfEdges.push(makeEdge(ie, theme, ie.type === 'loop' ? 'loop' : 'next'));
    }
  }
}

// ── Point d'entrée ─────────────────────────────────────────────────

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
  const cache = createSizeCache();

  // 1) Tailles de tous les nœuds (bottom-up, mémoïsé).
  const sizeOfTop = (id: string): { w: number; h: number } => {
    const n = graph.nodes.find((x) => x.id === id);
    if (n) {
      const s = cache.compute(n, expanded, algo, dir);
      return { w: s.w, h: s.h };
    }
    return { w: NODE_W, h: NODE_H };
  };

  // 2) Layout TOP avec tailles réelles.
  const topIds = graph.nodes.map((n) => n.id);
  const topEdges = graph.edges.map((e) => ({ from: e.from, to: e.to }));
  const topPos = dagreLayout(topIds, topEdges, algo, dir, sizeOfTop);

  // 3) Nœuds TOP + descendants récursifs.
  for (const n of graph.nodes) {
    const pos = topPos.get(n.id) || { x: 0, y: 0 };
    const s = cache.compute(n, expanded, algo, dir);
    addNodeRecursive(n, rfNodes, rfEdges, theme, opts, pos, s, undefined, cache);
  }

  // 4) Arêtes TOP (externes) — ré-routées vers entrypoint/exitpoints si déplié.
  for (const e of graph.edges) {
    const src = graph.nodes.find((n) => n.id === e.from);
    const tgt = graph.nodes.find((n) => n.id === e.to);
    const srcExp = expanded.has(e.from) && !!src?.vars?.inner;
    const tgtExp = expanded.has(e.to) && !!tgt?.vars?.inner;
    const from = srcExp ? (src?.vars?.inner?.entrypoint ?? e.from) : e.from;
    const to = tgtExp ? (tgt?.vars?.inner?.entrypoint ?? e.to) : e.to;
    rfEdges.push(makeEdge({ from, to, label: e.label, type: e.type }, theme, e.type));
  }

  return { nodes: rfNodes, edges: rfEdges };
}

// ── Point d'entrée ─────────────────────────────────────────────────

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
