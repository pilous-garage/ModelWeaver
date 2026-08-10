// graphExpand.ts — rendu HIÉRARCHIQUE récursif du graphe (dépliage/fold).
//
// Transforme un GraphDoc (avec vars.inner) en nœuds/arêtes React Flow PLATS,
// selon l'état d'expansion :
//   - PLIÉ   : le nœud est seul ; ses arêtes internes ne sont PAS affichées.
//   - DÉPLIÉ : le nœud devient une BOX UNDERLAY (nœud de fond, z-index bas)
//              qui englobe ses sous-nœuds, PLACÉS EN POSITIONS ABSOLUES
//              (aplatis dans le graphe principal, pas de parentId React Flow
//              — plus fiable). Les arêtes internes apparaissent ; les arêtes
//              externes entrent par l'entrypoint et sortent par les exitpoints.
//
// Layout : précalcul bottom-up des tailles (feuilles → racine), puis dagre par
// niveau. Mémoïsation incrémentale (SizeCache).

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
  hideBoxFold?: boolean;   // mode taskflow : cacher les boutons fold des boxes
  onZip?: (id: string) => void;      // bouton − (nœud zipable)
  onUnzip?: (id: string) => void;    // bouton + (nœud unzipable)
}

interface Sizes {
  w: number;
  h: number;
  childPos: Map<string, { x: number; y: number }> | null; // relatif (sous header)
}

/**
 * Masque récursivement les nœuds `visible=false` et les arêtes liées.
 * Un box (nœud avec vars.inner) n'est affiché que si au moins un de ses nœuds
 * internes (place ou transition) est lui-même affiché.
 */
export function pruneInvisible(graph: GraphDoc): GraphDoc {
  const shown = new Map<string, boolean>();
  const isShown = (n: any): boolean => {
    if (!n || shown.has(n.id)) return !!shown.get(n?.id ?? '');
    let s: boolean;
    if (n.vars?.inner?.nodes?.length) {
      // Box : affichée ssi visible ET au moins un NŒUD interne visible — si
      // plus aucun nœud visible dedans, elle ne s'affiche plus (visible reste
      // true ; les arêtes vers l'intérieur sont prunes).
      const ivn = n.vars?.innerVisibleNodes;
      s = n.vars?.visible === false ? false
        : (typeof ivn === 'number' ? ivn >= 1 : n.vars.inner.nodes.some(isShown));
    } else {
      s = n.vars?.visible !== false;
    }
    shown.set(n.id, s);
    return s;
  };
  const pruneNodes = (nodes: any[]): any[] =>
    nodes.filter(isShown).map((n) =>
      n.vars?.inner?.nodes
        ? { ...n, vars: { ...n.vars, inner: { ...n.vars.inner, nodes: pruneNodes(n.vars.inner.nodes), edges: pruneEdges(n.vars.inner.nodes, n.vars.inner.edges) } } }
        : n);
  const pruneEdges = (nodes: any[], edges: any[]): any[] => {
    const ids = new Set(nodes.map((n) => n.id));
    // Garde une arête si au moins UNE extrémité est un nœud rendu du niveau :
    // les arêtes TRANS-FRONTÈRES (ex. break_done → after_loop, cible externe à
    // la box) ne doivent pas être supprimées — sinon le break semble sans
    // sortie. Elles ne sont rendues que si la box est dépliée.
    return edges.filter((e) => ids.has(e.from) || ids.has(e.to));
  };
  return { ...graph, nodes: pruneNodes(graph.nodes), edges: pruneEdges(graph.nodes, graph.edges) };
}

export interface SizeCache {
  map: Map<string, Sizes>;
  compute(node: GraphNode, expanded: Set<string>, algo: string, dir: string): Sizes;
  invalidate(node: GraphNode): void;
}

export function createSizeCache(): SizeCache {
  const map = new Map<string, Sizes>();
  const key = (node: GraphNode, algo: string, dir: string) => `${node.id}|${algo}|${dir}`;

  function compute(node: GraphNode, expanded: Set<string>, algo: string, dir: string): Sizes {
    const k = key(node, algo, dir);
    const cached = map.get(k);
    if (cached) return cached;
    let s: Sizes;
    if (!node.vars?.inner?.nodes?.length || !expanded.has(node.id)) {
      // Feuille : la hauteur/largeur suit le label (multi-lignes pour les
      // skills monolithiques dont les inputs sont dans le label). Les
      // transitions de Pétri sont des BARRES fines (hauteur réduite).
      if (node.type === 'transition') {
        s = { w: 10, h: 32, childPos: null };
      } else {
        const lines = String(node.label ?? '').split('\n');
        const maxLen = Math.max(...lines.map((l) => l.length), 0);
        s = {
          w: Math.max(NODE_W, Math.min(maxLen * 6 + 24, 340)),
          h: Math.max(NODE_H, lines.length * 15 + 10),
          childPos: null,
        };
      }
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
      for (const k of [...map.keys()]) {
        if (k.startsWith(node.id + '|')) map.delete(k);
      }
    },
  };
}

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

/**
 * Un nœud est DÉPLIABLE s'il a un sous-graphe NON VIDE. Les steps atomiques
 * (set_variable, end, break…) ont `vars.inner = { nodes: [] }` (posé par
 * fsmHierarchy) — ils ne doivent NI afficher de bouton + NI devenir des
 * containers vides quand on déplie.
 */
function hasInnerNodes(node: GraphNode | undefined): boolean {
  return !!(node?.vars?.inner?.nodes && node.vars.inner.nodes.length > 0);
}

/**
 * Ajoute un nœud + ses descendants DÉPLIÉS, en positions ABSOLUES (aplatis).
 * Le container déplié est un nœud de fond (zIndex -1) ; ses enfants sont des
 * nœuds normaux positionnés au-dessus (relatif + position du parent).
 */
function addNodeRecursive(
  node: GraphNode,
  rfNodes: RFNode[],
  rfEdges: RFEdge[],
  theme: ThemeGraphe,
  opts: ExpandOptions,
  absPos: { x: number; y: number },
  size: { w: number; h: number },
  cache: SizeCache,
): void {
  const st = nodeStyleOf(theme, node.type);
  const hasInner = hasInnerNodes(node);
  const isExpanded = opts.expanded.has(node.id) && hasInner;
  const data: any = {
    label: `${st.icon ?? ''} ${node.label}`,
    n: node, style: st,
    hasInner,
    expanded: isExpanded,
    dir: opts.dir ?? 'LR',
    onToggle: opts.onToggle,
    hideBoxFold: !!opts.hideBoxFold,
    onZip: opts.onZip,
    onUnzip: opts.onUnzip,
  };
  // Le container déplié est un nœud de FOND (z-index bas, non interactif).
  if (isExpanded) {
    rfNodes.push({
      id: node.id,
      type: 'flow',
      position: { x: absPos.x, y: absPos.y },
      data,
      style: { width: size.w, height: size.h },
      zIndex: -1,
    } as RFNode);
  } else {
    rfNodes.push({
      id: node.id,
      type: 'flow',
      position: { x: absPos.x, y: absPos.y },
      data,
      style: { width: size.w, height: size.h },
    } as RFNode);
  }

  // Sous-nœuds (positions absolues = relatif + position du container).
  if (isExpanded && node.vars?.inner) {
    const inner = node.vars.inner;
    const sizes = cache.compute(node, opts.expanded, opts.algo ?? 'dagre', opts.dir ?? 'LR');
    const childPos = sizes.childPos!;
    for (const sub of inner.nodes) {
      const rel = childPos.get(sub.id) || { x: 0, y: 0 };
      const abs = { x: absPos.x + rel.x, y: absPos.y + rel.y };
      const subSizes = cache.compute(sub, opts.expanded, opts.algo ?? 'dagre', opts.dir ?? 'LR');
      addNodeRecursive(sub, rfNodes, rfEdges, theme, opts, abs, subSizes, cache);
    }
    for (const ie of inner.edges) {
      rfEdges.push(makeEdge(ie, theme, ie.type === 'loop' ? 'loop' : 'next'));
    }
  }
}

export function buildExpandedGraph(
  graph: GraphDoc,
  opts: ExpandOptions,
): { nodes: RFNode[]; edges: RFEdge[] } {
  const algo = opts.algo ?? 'dagre';
  const dir = opts.dir ?? 'LR';
  const theme = opts.theme;
  const expanded = opts.expanded;
  const cache = createSizeCache();
  // Masque les nœuds `visible=false` et les arêtes liées. Un box (inner) n'est
  // affiché que s'il a du contenu visible (place ou transition interne visible).
  graph = pruneInvisible(graph);

  const rfNodes: RFNode[] = [];
  const rfEdges: RFEdge[] = [];

  // 1) Tailles bottom-up + layout TOP.
  const sizeOfTop = (id: string): { w: number; h: number } => {
    const n = graph.nodes.find((x) => x.id === id);
    if (n) {
      const s = cache.compute(n, expanded, algo, dir);
      return { w: s.w, h: s.h };
    }
    return { w: NODE_W, h: NODE_H };
  };
  const topIds = graph.nodes.map((n) => n.id);
  const topEdges = graph.edges.map((e) => ({ from: e.from, to: e.to }));
  const topPos = dagreLayout(topIds, topEdges, algo, dir, sizeOfTop);

  // Index plat de TOUS les nœuds (top + descendants) pour le routage récursif.
  const nodeById = new Map<string, GraphNode>();
  const collect = (n: GraphNode) => {
    nodeById.set(n.id, n);
    for (const s of n.vars?.inner?.nodes ?? []) collect(s);
  };
  graph.nodes.forEach(collect);

  // 2) Nœuds TOP + descendants récursifs (positions absolues).
  for (const n of graph.nodes) {
    const pos = topPos.get(n.id) || { x: 0, y: 0 };
    const s = cache.compute(n, expanded, algo, dir);
    addNodeRecursive(n, rfNodes, rfEdges, theme, opts, pos, s, cache);
  }

  // 3) Arêtes TOP (externes) — brutes, routées ensuite.
  for (const e of graph.edges) {
    rfEdges.push(makeEdge({ from: e.from, to: e.to, label: e.label, type: e.type }, theme, e.type));
  }

  // 4) Passe de routage récursif sur TOUTES les arêtes (externes + internes) :
  //    une source dépliée sort par SES exitpoints (FAN-OUT : un while sort par
  //    sa condition/exit ET par ses breaks), une cible dépliée entre par son
  //    entrypoint — récursivement.
  const fanOut = (id: string, isExit: boolean, depth = 0): string[] => {
    const n = nodeById.get(id);
    if (!n || !expanded.has(n.id) || !hasInnerNodes(n) || depth > 12) return [id];
    const inner = n.vars!.inner!;
    const next = isExit ? (inner.exitpoints ?? []) : [inner.entrypoint];
    const ids = next.length ? next : [inner.nodes?.[0]?.id];
    return ids.filter(Boolean).flatMap((t: string) => fanOut(t, isExit, depth + 1));
  };
  const fanned: RFEdge[] = [];
  for (const e of rfEdges) {
    const srcs = fanOut(e.source, true);
    const tgts = fanOut(e.target, false);
    for (const s of srcs) {
      for (const t of tgts) {
        // id UNIQUE par (source→cible) réels : le fan-out ne doit PAS garder
        // l'id du parent (sinon plusieurs arêtes `condition/break → after_loop`
        // portent la même clé `working_loop->after_loop` → React Flow n'en rend
        // qu'une seule et les autres disparaissent).
        fanned.push({ ...e, source: s, target: t, id: `${s}->${t}` });
      }
    }
  }

  // 5) Déduplication : une arête top routée vers l'entrypoint/exitpoint d'un
  //    container déplié duplique l'arête interne du sous-graphe (ex. le switch
  //    a ses branches au top ET dans son inner). On garde la première.
  const seen = new Set<string>();
  const uniq: RFEdge[] = [];
  for (const e of fanned) {
    const key = `${e.source}|${e.target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    uniq.push(e);
  }

  return { nodes: rfNodes, edges: uniq };
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
