import { describe, it, expect } from 'vitest';
import {
  computeEdgePorts, toBoxes, dominantSide, opposite, anchorPoint,
} from '../theme_graphe/graphLayout.ts';

const W = 150, H = 44;

function boxesOf(p: Record<string, [number, number]>): Map<string, any> {
  return toBoxes(
    new Map(Object.entries(p).map(([id, [x, y]]) => [id, { x, y }])),
    () => [W, H] as [number, number],
    Object.keys(p));
}

describe('graphLayout — layout par côtés', () => {
  it('arête vers la droite → source E, target W', () => {
    // A à gauche, B à droite (même ligne)
    const boxes = boxesOf({ A: [0, 0], B: [300, 0] });
    const ports = computeEdgePorts(boxes, [{ from: 'A', to: 'B' }]);
    expect(ports[0].sourceSide).toBe('e');
    expect(ports[0].targetSide).toBe('w');
  });

  it('arête vers le bas → source S, target N', () => {
    const boxes = boxesOf({ A: [0, 0], B: [0, 300] });
    const ports = computeEdgePorts(boxes, [{ from: 'A', to: 'B' }]);
    expect(ports[0].sourceSide).toBe('s');
    expect(ports[0].targetSide).toBe('n');
  });

  it('dominantSide + opposite', () => {
    expect(dominantSide(0, 0, 100, 10)).toBe('e');
    expect(dominantSide(0, 0, -100, 10)).toBe('w');
    expect(opposite('e')).toBe('w');
    expect(opposite('n')).toBe('s');
  });

  it('répartit les arêtes du même côté (spread)', () => {
    const boxes = boxesOf({ A: [0, 0], B1: [300, 0], B2: [300, 100], B3: [300, 200] });
    const ports = computeEdgePorts(boxes, [
      { from: 'A', to: 'B1' }, { from: 'A', to: 'B2' }, { from: 'A', to: 'B3' },
    ]);
    // Les 3 sortent par E de A, réparties le long du bord.
    const ePorts = ports.filter((p) => p.sourceSide === 'e');
    expect(ePorts.length).toBe(3);
    const pos = ePorts.map((p) => p.sourcePos);
    expect(Math.max(...pos) - Math.min(...pos)).toBeGreaterThan(0.1);
  });

  it('anchorPoint retourne un point sur la bordure', () => {
    const box = { id: 'A', x: 0, y: 0, w: 150, h: 44 };
    const [x, y] = anchorPoint(box, 'e', 0.5);
    expect(x).toBe(150);      // bord droit
    expect(y).toBe(22);       // milieu
  });
});
