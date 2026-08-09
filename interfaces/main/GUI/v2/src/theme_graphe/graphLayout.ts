// graphLayout.ts — layout géométrique par CÔTÉS.
// Au lieu d'un point d'entrée/sortie unique par nœud, chaque arête sort/entre
// par le côté (N/S/E/W) le plus proche de sa cible/source, et les extrémités
// sont réparties le long de la bordure (milieu, quart, trois-quarts…).
//
// Le principe (vision utilisateur) :
//   1. Pour chaque nœud, on détermine ses côtés d'entrée et de sortie en
//      fonction de la direction des voisins (le maximum d'arêtes du bon côté).
//   2. Chaque arête d'un nœud sort par le côté qui pointe vers sa cible.
//   3. Sur un même côté, les extrémités sont espacées (milieu → quart…).

export type Side = 'n' | 's' | 'e' | 'w';

export interface NodeBox {
  id: string;
  x: number;   // top-left
  y: number;
  w: number;
  h: number;
}

export interface EdgePorts {
  from: string;
  to: string;
  sourceSide: Side;
  targetSide: Side;
  // Position relative le long du bord (0=coin, 0.5=milieu, 1=coin) pour
  // espacer plusieurs arêtes du même côté.
  sourcePos: number;
  targetPos: number;
}

export const SIDE_VECTOR: Record<Side, [number, number]> = {
  n: [0, -1],
  s: [0, 1],
  e: [1, 0],
  w: [-1, 0],
};

/** Direction dominante d'un nœud A vers B (en fonction de la géométrie). */
export function dominantSide(ax: number, ay: number, bx: number, by: number): Side {
  const dx = bx - ax;
  const dy = by - ay;
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? 'e' : 'w';
  }
  return dy >= 0 ? 's' : 'n';
}

/**
 * Calcule les côtés + positions des extrémités pour chaque arête, à partir
 * des boîtes des nœuds.
 *
 * Règle de répartition : pour chaque nœud, on groupe ses arêtes par côté de
 * sortie/entrée, et on les espace le long du bord (index / count → position
 * entre 0.2 et 0.8, pour éviter les coins).
 */
export function computeEdgePorts(
  boxes: Map<string, NodeBox>,
  edges: { from: string; to: string }[],
): EdgePorts[] {
  // Prépare le comptage par (nodeId, côté) pour l'espacement.
  const outCount = new Map<string, number>(); // key nodeId:side
  const inCount = new Map<string, number>();

  // Première passe : déterminer les côtés bruts.
  const raw: { e: { from: string; to: string; s: Side; t: Side } }[] = [];
  for (const ed of edges) {
    const a = boxes.get(ed.from);
    const b = boxes.get(ed.to);
    if (!a || !b) continue;
    const ax = a.x + a.w / 2, ay = a.y + a.h / 2;
    const bx = b.x + b.w / 2, by = b.y + b.h / 2;
    // Côté de sortie sur A : vers B. Côté d'entrée sur B : le côté qui fait
    // face à A (dominantSide depuis B vers A).
    const s = dominantSide(ax, ay, bx, by);
    const t = dominantSide(bx, by, ax, ay);
    raw.push({ e: { from: ed.from, to: ed.to, s, t } });
    const kOut = `${ed.from}:${s}`;
    const kIn = `${ed.to}:${t}`;
    outCount.set(kOut, (outCount.get(kOut) ?? 0) + 1);
    inCount.set(kIn, (inCount.get(kIn) ?? 0) + 1);
  }

  // Deuxième passe : assigner la position le long du bord.
  const outIdx = new Map<string, number>();
  const inIdx = new Map<string, number>();
  const out: EdgePorts[] = [];
  for (const { e } of raw) {
    const kOut = `${e.from}:${e.s}`;
    const kIn = `${e.to}:${e.t}`;
    const iOut = outIdx.get(kOut) ?? 0;
    const iIn = inIdx.get(kIn) ?? 0;
    outIdx.set(kOut, iOut + 1);
    inIdx.set(kIn, iIn + 1);
    const cOut = outCount.get(kOut) ?? 1;
    const cIn = inCount.get(kIn) ?? 1;
    out.push({
      from: e.from, to: e.to,
      sourceSide: e.s, targetSide: e.t,
      sourcePos: spread(iOut, cOut),
      targetPos: spread(iIn, cIn),
    });
  }
  return out;
}

/** Répartit n points entre 0.2 et 0.8 le long du bord (évite les coins). */
export function spread(index: number, count: number): number {
  if (count <= 1) return 0.5;
  if (count === 2) return index === 0 ? 0.35 : 0.65;
  return 0.2 + (index / (count - 1)) * 0.6;
}

/** Point d'ancrage sur le bord d'une boîte, pour un côté + position (0..1). */
export function anchorPoint(box: NodeBox, side: Side, pos: number): [number, number] {
  const px = box.x + (box.w * pos);
  const py = box.y + (box.h * pos);
  switch (side) {
    case 'n': return [px, box.y];
    case 's': return [px, box.y + box.h];
    case 'e': return [box.x + box.w, py];
    case 'w': return [box.x, py];
  }
}

/** Côté opposé. */
export function opposite(s: Side): Side {
  return ({ n: 's', s: 'n', e: 'w', w: 'e' } as Record<Side, Side>)[s];
}

/** Extrait les boîtes depuis un map de positions (centre) + tailles. */
export function toBoxes(
  pos: Map<string, { x: number; y: number }>,
  sizeOf: (id: string) => [number, number],
  ids: string[],
): Map<string, NodeBox> {
  const m = new Map<string, NodeBox>();
  for (const id of ids) {
    const p = pos.get(id);
    if (!p) continue;
    const [w, h] = sizeOf(id);
    m.set(id, { id, x: p.x, y: p.y, w, h });
  }
  return m;
}
