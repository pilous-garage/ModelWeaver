import { describe, it, expect } from 'vitest';
import { buildExpandedGraph, pruneInvisible } from './graphExpand.ts';
import { loadTheme } from './themeGraphe.ts';
describe('zIndex bouton +', () => {
  it('box repliée → zIndex 100 ; box dépliée → -1', () => {
    const g: any = {
      nodes: [
        { id: 'A', type: 'place', vars: { visible: true } },
        { id: 'box', type: 'flow', vars: { visible: true, inner: { nodes: [{ id: 'box/x', type: 'place', vars: { visible: true } }], edges: [], entrypoint: 'box/x', exitpoints: ['box/x'] } } },
        { id: 'B', type: 'place', vars: { visible: true } },
      ],
      edges: [{ from: 'A', to: 'box' }, { from: 'box', to: 'B' }],
    };
    const r1 = buildExpandedGraph(pruneInvisible(g), { theme: loadTheme(null), expanded: new Set(), onToggle: () => {}, hideBoxFold: false });
    console.log('plié:', r1.nodes.map((n: any) => `${n.id}:${n.zIndex}`).join(', '));
    expect(r1.nodes.find((n: any) => n.id === 'box')?.zIndex).toBe(100);
    const r2 = buildExpandedGraph(pruneInvisible(g), { theme: loadTheme(null), expanded: new Set(['box']), onToggle: () => {}, hideBoxFold: false });
    console.log('déplié:', r2.nodes.map((n: any) => `${n.id}:${n.zIndex}`).join(', '));
    expect(r2.nodes.find((n: any) => n.id === 'box')?.zIndex).toBe(-1);
  });
});
