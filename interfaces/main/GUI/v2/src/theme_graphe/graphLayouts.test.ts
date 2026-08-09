import { describe, it, expect } from 'vitest';
import {
  layoutByAlgo, LAYOUT_ALGOS, LAYOUT_DIRS,
} from '../theme_graphe/graphLayouts.ts';

describe('graphLayouts — multi-algos × directions', () => {
  it('retourne des positions pour chaque nœud (LR)', () => {
    const pos = layoutByAlgo(
      [{ id: 'A' }, { id: 'B' }, { id: 'C' }],
      [{ from: 'A', to: 'B' }, { from: 'B', to: 'C' }],
      'dagre', 'LR',
    );
    expect(pos.size).toBe(3);
    // LR : A avant B avant C en x.
    expect(pos.get('A')!.x).toBeLessThan(pos.get('B')!.x);
    expect(pos.get('B')!.x).toBeLessThan(pos.get('C')!.x);
  });

  it('supporte les 3 algos et les 4 directions', () => {
    const nodes = [{ id: 'A' }, { id: 'B' }];
    const edges = [{ from: 'A', to: 'B' }];
    for (const algo of LAYOUT_ALGOS) {
      for (const dir of LAYOUT_DIRS) {
        const pos = layoutByAlgo(nodes, edges, algo, dir);
        expect(pos.size).toBe(2);
        expect(pos.get('A')).toBeDefined();
        expect(pos.get('B')).toBeDefined();
      }
    }
  });

  it('compacte les boucles (back-edge rapprochée en LR)', () => {
    // A → B → C, puis C → A (boucle). En LR, C revient vers A.
    const pos = layoutByAlgo(
      [{ id: 'A' }, { id: 'B' }, { id: 'C' }],
      [{ from: 'A', to: 'B' }, { from: 'B', to: 'C' }, { from: 'C', to: 'A' }],
      'compact', 'LR',
    );
    expect(pos.size).toBe(3);
    // Le compactage ne doit pas planter et produit des positions finies.
    for (const [id, p] of pos) {
      expect(Number.isFinite(p.x)).toBe(true);
      expect(Number.isFinite(p.y)).toBe(true);
      expect(p).toBeDefined();
    }
  });
});
