// graphLayouts.ts — algos de layout de graphe (positions auto).
// Plusieurs algo (dagre standard, compact, simplex) × 4 directions.
// Le compactage des boucles : les algos "compact" réduisent les espacements
// et rapprochent les arêtes de retour (back-edges) pour réduire l'étalement.
//
// API : layoutByAlgo(nodes, edges, algo, dir) → Map<nodeId, {x,y}> (top-left).

import dagre from 'dagre';

export type LayoutAlgo = 'dagre' | 'compact' | 'simplex';
export type LayoutDir = 'LR' | 'RL' | 'TB' | 'BT';

export const LAYOUT_ALGOS: LayoutAlgo[] = ['dagre', 'compact', 'simplex'];
export const LAYOUT_DIRS: LayoutDir[] = ['LR', 'RL', 'TB', 'BT'];

export interface LayoutOptions {
  algo: LayoutAlgo;
  dir: LayoutDir;
}

// Paramètres dagre par algo : le compact resserre (boucles + densité).
const PARAMS: Record<LayoutAlgo, { nodesep: number; ranksep: number; ranker?: string }> = {
  dagre:   { nodesep: 30, ranksep: 60 },
  compact: { nodesep: 12, ranksep: 28, ranker: 'tight-tree' },
  simplex: { nodesep: 30, ranksep: 60, ranker: 'network-simplex' },
};

const NODE_W = 150;
const NODE_H = 44;

export function layoutByAlgo(
  nodes: { id: string }[],
  edges: { from: string; to: string }[],
  algo: LayoutAlgo,
  dir: LayoutDir,
): Map<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  const p = PARAMS[algo] ?? PARAMS.dagre;
  g.setGraph({ rankdir: dir, nodesep: p.nodesep, ranksep: p.ranksep, ranker: p.ranker });
  for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  for (const e of edges) {
    if (e.from && e.to) g.setEdge(e.from, e.to);
  }
  dagre.layout(g);
  const pos = new Map<string, { x: number; y: number }>();
  for (const n of nodes) {
    const q = g.node(n.id) as { x?: number; y?: number } | undefined;
    pos.set(n.id, {
      x: ((q?.x ?? 0) - NODE_W / 2) || 0,
      y: ((q?.y ?? 0) - NODE_H / 2) || 0,
    });
  }
  // Post-traitement compact : rapprocher les arêtes de retour (back-edges) —
  // pour un graphe à boucles (ex. while), le nœud de retour est éloigné par
  // dagre ; on le ramène près de son prédécesseur si l'espace le permet.
  if (algo === 'compact') {
    compactCycles(pos, edges, dir);
  }
  return pos;
}

/**
 * Compactage des cycles : pour chaque arête qui « revient en arrière » dans la
 * direction courante (back-edge), on attire le nœud cible vers la ligne de son
 * prédécesseur (le long de l'axe orthogonal à la direction). Réduit
 * l'étalement orthogonal des boucles (ex. un while qui boucle).
 */
function compactCycles(
  pos: Map<string, { x: number; y: number }>,
  edges: { from: string; to: string }[],
  dir: LayoutDir,
): void {
  // Axe principal (avant) : x pour LR/RL, y pour TB/BT.
  const isHorizontal = dir === 'LR' || dir === 'RL';
  const positive = dir === 'LR' || dir === 'TB';
  const mainOf = (p: { x: number; y: number }) => (isHorizontal ? p.x : p.y);
  const orthoOf = (p: { x: number; y: number }) => (isHorizontal ? p.y : p.x);
  const setOrtho = (p: { x: number; y: number }, v: number) =>
    isHorizontal ? { x: p.x, y: v } : { x: v, y: p.y };

  // Back-edge : la cible est DERRIÈRE la source sur l'axe principal.
  const back = edges.filter((e) => {
    const a = pos.get(e.from); const b = pos.get(e.to);
    if (!a || !b) return false;
    const d = mainOf(b) - mainOf(a);
    return positive ? d < 0 : d > 0;
  });
  if (back.length === 0) return;

  // Grouper par source : chaque source attire ses cibles de retour sur sa ligne.
  const bySource = new Map<string, string[]>();
  for (const e of back) {
    const arr = bySource.get(e.from) ?? [];
    if (!arr.includes(e.to)) arr.push(e.to);
    bySource.set(e.from, arr);
  }
  for (const [srcId, targets] of bySource) {
    const s = pos.get(srcId); if (!s) continue;
    // Rapprocher chaque cible de la ligne de la source (orthogonal).
    for (const tid of targets) {
      const t = pos.get(tid); if (!t) continue;
      if (Math.abs(orthoOf(t) - orthoOf(s)) > NODE_H * 0.6) {
        pos.set(tid, setOrtho(t, orthoOf(s)));
      }
    }
  }
}
