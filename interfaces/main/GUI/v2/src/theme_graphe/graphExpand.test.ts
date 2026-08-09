import { describe, it, expect } from 'vitest';
import { buildExpandedGraph } from '../theme_graphe/graphExpand.ts';
import { loadTheme } from '../theme_graphe/themeGraphe.ts';

const theme = loadTheme(null);

function makeGraph() {
  return {
    title: 't',
    nodes: [
      { id: 'A', type: 'skill', label: 'A', vars: {} },
      { id: 'loop', type: 'loop', label: 'loop', vars: { inner: {
        nodes: [
          { id: 'loop/condition', type: 'condition', label: 'x<3' },
          { id: 'loop/do', type: 'llm', label: 'do' },
        ],
        edges: [{ from: 'loop/condition', to: 'loop/do', type: 'loop' }],
        entrypoint: 'loop/condition',
        exitpoints: ['loop/do'],
      } } },
      { id: 'B', type: 'exitpoint', label: 'B', vars: {} },
    ],
    edges: [
      { from: 'A', to: 'loop', type: 'next' },
      { from: 'loop', to: 'B', type: 'next' },
    ],
  };
}

describe('buildExpandedGraph — dépliage hiérarchique', () => {
  it('pliée : sous-nœuds absents, pas d\'arêtes internes, arêtes externes sur le parent', () => {
    const r = buildExpandedGraph(makeGraph() as any, { theme, expanded: new Set(), onToggle: () => {} });
    const ids = r.nodes.map((n: any) => n.id);
    expect(ids).toContain('A');
    expect(ids).toContain('loop');
    expect(ids).toContain('B');
    // Pas de sous-nœuds internes
    expect(ids).not.toContain('loop/condition');
    expect(ids).not.toContain('loop/do');
    // Arêtes externes sur le parent, PAS d'arêtes internes
    expect(r.edges.some((e: any) => e.source === 'A' && e.target === 'loop')).toBe(true);
    expect(r.edges.some((e: any) => e.source === 'loop' && e.target === 'B')).toBe(true);
    expect(r.edges.some((e: any) => e.source === 'loop/condition')).toBe(false);
  });

  it('dépliée : sous-nœuds avec parentId, arêtes internes, arêtes externes ré-routées vers entrypoint', () => {
    const r = buildExpandedGraph(makeGraph() as any, {
      theme, expanded: new Set(['loop']), onToggle: () => {},
    });
    const ids = r.nodes.map((n: any) => n.id);
    // Sous-nœuds présents avec parentId = loop
    expect(ids).toContain('loop/condition');
    expect(ids).toContain('loop/do');
    const cond = r.nodes.find((n: any) => n.id === 'loop/condition');
    expect(cond?.parentId).toBe('loop');
    // Arête interne présente (condition → do)
    expect(r.edges.some((e: any) => e.source === 'loop/condition' && e.target === 'loop/do')).toBe(true);
    // Arête externe A→loop ré-routée vers l'entrypoint interne (condition)
    expect(r.edges.some((e: any) => e.target === 'loop/condition')).toBe(true);
  });

  it('les containers dépliés ne se superposent pas (dagre taille réelle)', () => {
    // Deux containers dépliés en parallèle : A(→loop1), A(→loop2).
    const g = {
      nodes: [
        { id: 'A', type: 'skill', label: 'A' },
        { id: 'loop1', type: 'loop', vars: { inner: {
          nodes: [{ id: 'loop1/x', type: 'llm' }], edges: [],
          entrypoint: 'loop1/x', exitpoints: ['loop1/x'],
        } } },
        { id: 'loop2', type: 'loop', vars: { inner: {
          nodes: [{ id: 'loop2/x', type: 'llm' }], edges: [],
          entrypoint: 'loop2/x', exitpoints: ['loop2/x'],
        } } },
      ],
      edges: [
        { from: 'A', to: 'loop1' }, { from: 'A', to: 'loop2' },
      ],
    };
    const r = buildExpandedGraph(g as any, {
      theme, expanded: new Set(['loop1', 'loop2']), onToggle: () => {},
    });
    const get = (id: string) => {
      const n = r.nodes.find((x: any) => x.id === id)!;
      return { x: n.position.x, y: n.position.y, w: (n.style as any)?.width, h: (n.style as any)?.height };
    };
    const a = get('loop1'), b = get('loop2');
    // Intersection de rectangles : pas de chevauchement.
    const overlap = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    expect(overlap).toBe(false);
  });
});
