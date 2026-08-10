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
  it('route les arêtes externes vers l\'entrypoint interne (routage RÉCURSIF)', () => {
    // A → pick (skill dépliable avec un nœud skill interne lui-même dépliable)
    // → B. Quand pick est déplié, l'arête doit viser l'entrypoint interne et
    // PAS la box (qui n'a pas de handles → flèche disparue).
    const g = {
      nodes: [
        { id: 'A', type: 'step', label: 'A', vars: {} },
        { id: 'pick', type: 'skill', label: 'pick', ref: 'workspace/token_task_pick@v1',
          vars: { inner: {
            nodes: [
              { id: 'pick/skill', type: 'skill', ref: 'workspace/token_task_pick@v1', label: 'token_task_pick',
                vars: { inner: {
                  nodes: [{ id: 'pick/skill/in', type: 'skill_input', label: 'inputs' }],
                  edges: [], entrypoint: 'pick/skill/in', exitpoints: ['pick/skill/in'],
                } } },
            ],
            edges: [], entrypoint: 'pick/skill', exitpoints: ['pick/skill'],
          } } },
        { id: 'B', type: 'step', label: 'B', vars: {} },
      ],
      edges: [
        { from: 'A', to: 'pick', type: 'next' },
        { from: 'pick', to: 'B', type: 'next' },
      ],
    };
    // pick déplié SEUL : les arêtes visent pick/skill (l'entrypoint rendu).
    const r1 = buildExpandedGraph(g as any, {
      theme, expanded: new Set(['pick']), onToggle: () => {},
    });
    expect(r1.edges.find((e: any) => e.source === 'A')?.target).toBe('pick/skill');
    expect(r1.edges.find((e: any) => e.target === 'B')?.source).toBe('pick/skill');
    // pick ET pick/skill dépliés : descente jusqu'à pick/skill/in.
    const r2 = buildExpandedGraph(g as any, {
      theme, expanded: new Set(['pick', 'pick/skill']), onToggle: () => {},
    });
    expect(r2.edges.find((e: any) => e.source === 'A')?.target).toBe('pick/skill/in');
    expect(r2.edges.find((e: any) => e.target === 'B')?.source).toBe('pick/skill/in');
  });

  it('les steps atomiques (vars.inner.nodes vide) ne sont pas des containers', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'skill', label: 'A', vars: { inner: { nodes: [], edges: [] } } },
        { id: 'B', type: 'flow', label: 'B', vars: { inner: {
          nodes: [{ id: 'B/x', type: 'step', vars: {} }], edges: [],
        } } },
      ],
      edges: [{ from: 'A', to: 'B' }],
    };
    // "Tout déplier" : A (vide) ne doit pas être déplié, B oui.
    const r = buildExpandedGraph(g as any, {
      theme, expanded: new Set(['A', 'B']), onToggle: () => {},
    });
    const a = r.nodes.find((n: any) => n.id === 'A');
    expect(a?.zIndex).toBeUndefined();       // pas une box de fond
    expect(a?.data.hasInner).toBe(false);    // pas de bouton +
    expect(r.nodes.find((n: any) => n.id === 'B')?.zIndex).toBe(-1); // box
    expect(r.nodes.find((n: any) => n.id === 'B/x')).toBeTruthy();   // sous-nœud rendu
  });

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

  it('dépliée : container aplati (zIndex -1) + sous-nœuds en positions absolues, arêtes internes, externes ré-routées vers entrypoint', () => {
    const r = buildExpandedGraph(makeGraph() as any, {
      theme, expanded: new Set(['loop']), onToggle: () => {},
    });
    const ids = r.nodes.map((n: any) => n.id);
    // Sous-nœuds présents (aplatis, SANS parentId — positions absolues)
    expect(ids).toContain('loop/condition');
    expect(ids).toContain('loop/do');
    const loop = r.nodes.find((n: any) => n.id === 'loop');
    const cond = r.nodes.find((n: any) => n.id === 'loop/condition');
    // Le container déplié devient une box de fond (z-index bas).
    expect(loop?.zIndex).toBe(-1);
    // Les enfants n'ont PAS parentId mais des positions absolues non nulles.
    expect(cond?.parentId).toBeUndefined();
    expect(cond?.position.x).toBeGreaterThan(0);
    expect(cond?.position.y).toBeGreaterThan(0);
    // La box du container englobe ses enfants.
    const loopW = (loop?.style as any)?.width as number;
    expect(cond!.position.x).toBeLessThan((loop!.position.x) + loopW);
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

  it('déplie récursivement niveau 2 (container imbriqué)', () => {
    // outer (container) → inner2 (container) → leaf.
    const g = {
      nodes: [{
        id: 'outer', type: 'flow', vars: { inner: {
          nodes: [{
            id: 'inner2', type: 'flow', vars: { inner: {
              nodes: [{ id: 'leaf', type: 'llm' }],
              edges: [], entrypoint: 'leaf', exitpoints: ['leaf'],
            } },
          }],
          edges: [], entrypoint: 'inner2', exitpoints: ['inner2'],
        } },
      }],
      edges: [],
    };
    const r = buildExpandedGraph(g as any, {
      theme, expanded: new Set(['outer', 'inner2']), onToggle: () => {},
    });
    const ids = r.nodes.map((n: any) => n.id);
    expect(ids).toContain('outer');
    expect(ids).toContain('inner2');
    expect(ids).toContain('leaf');
    // Containers dépliés = boxes de fond ; leaf aplati (pas de parentId).
    const outer = r.nodes.find((n: any) => n.id === 'outer');
    const i2 = r.nodes.find((n: any) => n.id === 'inner2');
    const lf = r.nodes.find((n: any) => n.id === 'leaf');
    expect(outer?.zIndex).toBe(-1);
    expect(i2?.zIndex).toBe(-1);
    expect(lf?.parentId).toBeUndefined();
    // leaf est DANS l'étendue de inner2 qui est DANS l'étendue de outer.
    const o = { x: outer!.position.x, y: outer!.position.y, w: (outer!.style as any).width, h: (outer!.style as any).height };
    const inn = { x: i2!.position.x, y: i2!.position.y, w: (i2!.style as any).width, h: (i2!.style as any).height };
    expect(lf!.position.x).toBeGreaterThan(inn.x);
    expect(lf!.position.x).toBeLessThan(inn.x + inn.w);
    expect(lf!.position.y).toBeGreaterThan(inn.y);
    expect(lf!.position.y).toBeLessThan(inn.y + inn.h);
    expect(inn.x).toBeGreaterThanOrEqual(o.x);
    expect(inn.y).toBeGreaterThanOrEqual(o.y);
    expect(inn.x + inn.w).toBeLessThanOrEqual(o.x + o.w);
    // leaf a une position non nulle (pas de cadre vide).
    expect(lf?.position.x).toBeGreaterThan(0);
    expect(lf?.position.y).toBeGreaterThan(0);
  });
});
