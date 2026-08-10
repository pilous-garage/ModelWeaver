import { describe, it, expect } from 'vitest';
import {
  findClippable, zip, unzip, zipAll, unzipAll, createClipTable, rebuildState, recheckNode,
} from '../panels/taskflowClip.ts';
import { pruneInvisible } from '../theme_graphe/graphExpand.ts';

function mk(visible = true) { return { vars: { visible } }; }

describe('taskflowClip — règles de clip', () => {
  it('seq : tr_1 → P → tr_2 = tr_3 (P invisible, nœud créé connecté)', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'place', ...mk() },
        { id: 't1', type: 'transition', ...mk() },
        { id: 'P', type: 'place', ...mk() },
        { id: 't2', type: 'transition', ...mk() },
        { id: 'B', type: 'place', ...mk() },
      ],
      edges: [
        { from: 'A', to: 't1', type: 'next' },
        { from: 't1', to: 'P', type: 'next' },
        { from: 'P', to: 't2', type: 'next' },
        { from: 't2', to: 'B', type: 'next' },
      ],
    };
    const clips = findClippable(g);
    // seq généralisé : les transitions (t1/t2) sont aussi clippables — on
    // cible le clip de la PLACE P (bouton sur P).
    const seq = clips.find((c) => c.type === 'seq' && c.on.includes('P'));
    expect(seq).toBeTruthy();
    expect(seq!.zipped).toEqual(['t1', 'P', 't2']);
    // Le bouton − est sur la PLACE (P), pas sur les transitions.
    expect(seq!.on).toEqual(['P']);
    zip(g, seq!);
    // P invisible, nœud créé visible, A→z→B connectés.
    expect(g.nodes.find((n) => n.id === 'P')!.vars.visible).toBe(false);
    const z = g.nodes.find((n) => n.id === seq!.newId);
    expect(z!.vars.visible).toBe(true);
    expect(g.edges.some((e) => e.from === 'A' && e.to === seq!.newId)).toBe(true);
    expect(g.edges.some((e) => e.from === seq!.newId && e.to === 'B')).toBe(true);
    // Le rendu masque P et garde A → z → B.
    const pruned = pruneInvisible(g as any);
    expect(pruned.nodes.some((n) => n.id === 'P')).toBe(false);
    expect(pruned.nodes.some((n) => n.id === seq!.newId)).toBe(true);
    expect(pruned.edges.some((e) => e.from === 'A' && e.to === seq!.newId)).toBe(true);
    // unzip : restaure P, supprime z.
    expect(unzip(g, seq!)).toBe(true);
    expect(g.nodes.some((n) => n.id === 'P')).toBe(true);
    expect(g.nodes.some((n) => n.id === seq!.newId)).toBe(false);
    expect(g.nodes.find((n) => n.id === 'P')!.vars.visible).toBe(true);
  });

  it('par : groupe de transitions mêmes in/out → 1 (une seule place copiée)', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'place', ...mk() },
        { id: 't1', type: 'transition', ...mk() },
        { id: 't2', type: 'transition', ...mk() },
        { id: 't3', type: 'transition', ...mk() },
        { id: 'B', type: 'place', ...mk() },
      ],
      edges: [
        { from: 'A', to: 't1', type: 'next' }, { from: 't1', to: 'B', type: 'next' },
        { from: 'A', to: 't2', type: 'next' }, { from: 't2', to: 'B', type: 'next' },
        { from: 'A', to: 't3', type: 'next' }, { from: 't3', to: 'B', type: 'next' },
      ],
    };
    const clips = findClippable(g);
    const par = clips.find((c) => c.type === 'par');
    expect(par).toBeTruthy();
    expect(par!.zipped).toHaveLength(3);
    zip(g, par!);
    expect(g.nodes.filter((n) => ['t1', 't2', 't3'].includes(n.id)).every((n) => n.vars.visible === false)).toBe(true);
    expect(g.nodes.find((n) => n.id === par!.newId)!.vars.visible).toBe(true);
  });

  it('single : box avec exactement 1 place interne visible → clippable', () => {
    const g = {
      nodes: [
        { id: 'box', type: 'flow', ...mk(), vars: { visible: true, inner: { nodes: [
          { id: 'p1', type: 'place', ...mk() },
          { id: 'ti', type: 'transition', ...mk() },
        ] } } },
        { id: 'B', type: 'place', ...mk() },
      ],
      edges: [
        { from: 'p1', to: 'ti', type: 'next' },
        { from: 'ti', to: 'B', type: 'next' },
        { from: 'box', to: 'B', type: 'next' },
      ],
    };
    const clips = findClippable(g);
    const single = clips.find((c) => c.type === 'single');
    expect(single).toBeTruthy();
    // La place interne (pas la transition) est zippée.
    expect(single!.zipped).toEqual(['p1']);
  });

  it('les POTs à token ne sont pas clippables, mais les steps token (tokenOp) le sont', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'place', ...mk() },
        { id: 't1', type: 'transition', ...mk() },
        { id: 'pick', type: 'skill', ...mk(), vars: { visible: true, tokenOp: 'consume' } },
        { id: 't2', type: 'transition', ...mk() },
        { id: 'B', type: 'place', ...mk() },
        { id: 'token:coding', type: 'place', vars: { visible: true, token: true } },
      ],
      edges: [
        { from: 'A', to: 't1', type: 'next' },
        { from: 't1', to: 'pick', type: 'next' },
        { from: 'pick', to: 't2', type: 'next' },
        { from: 't2', to: 'B', type: 'next' },
        { from: 'token:coding', to: 't1', type: 'token' },
      ],
    };
    const clips = findClippable(g);
    // pick (tokenOp=consume) est un STEP normal → clippable (seq).
    expect(clips.some((c) => c.type === 'seq' && c.on.includes('pick'))).toBe(true);
    // La place de token (pot, vars.token) n'est JAMAIS zippée.
    expect(clips.some((c) => c.zipped.includes('token:coding'))).toBe(false);
  });

  it('récursif : un clip détecté DANS une box a le bon path + newId préfixé', () => {
    const g: any = {
      nodes: [
        { id: 'box', type: 'flow', vars: { visible: true, inner: { nodes: [
          { id: 'A', type: 'place', ...mk() },
          { id: 't1', type: 'transition', ...mk() },
          { id: 'P', type: 'place', ...mk() },
          { id: 't2', type: 'transition', ...mk() },
          { id: 'B', type: 'place', ...mk() },
        ], edges: [
          { from: 'A', to: 't1', type: 'next' },
          { from: 't1', to: 'P', type: 'next' },
          { from: 'P', to: 't2', type: 'next' },
          { from: 't2', to: 'B', type: 'next' },
        ] } } },
      ],
      edges: [],
    };
    const clips = findClippable(g);
    const seq = clips.find((c) => c.type === 'seq' && c.on.includes('P'));
    expect(seq).toBeTruthy();
    expect(seq!.path).toEqual(['box']);
    expect(seq!.newId).toBe('box/z:P');
    // zip dans l'inner : P invisible dans box.vars.inner.
    zip(g, seq!);
    const inner = g.nodes.find((n: any) => n.id === 'box').vars.inner;
    expect(inner.nodes.find((n: any) => n.id === 'P').vars.visible).toBe(false);
    expect(inner.nodes.some((n: any) => n.id === seq!.newId)).toBe(true);
    // unzip : restaure P.
    expect(unzip(g, seq!)).toBe(true);
    expect(inner.nodes.find((n: any) => n.id === 'P').vars.visible).toBe(true);
    expect(inner.nodes.some((n: any) => n.id === seq!.newId)).toBe(false);
  });

  it('seq généralisé : une TRANSITION intérieure (1 in/1 out) est clippable', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'place', ...mk() },
        { id: 't1', type: 'transition', ...mk() },
        { id: 'P', type: 'place', ...mk() },
      ],
      edges: [
        { from: 'A', to: 't1', type: 'next' },
        { from: 't1', to: 'P', type: 'next' },
      ],
    };
    const clips = findClippable(g);
    const seq = clips.find((c) => c.type === 'seq' && c.on.includes('t1'));
    expect(seq).toBeTruthy();
    expect(seq!.zipped).toEqual(['A', 't1', 'P']);
  });

  it('seq : une transition vers un POT à token n\'est PAS clippable (on ne masque pas un pot)', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'entrypoint', tags: ['entrypoint'], ...mk() },
        { id: 't', type: 'transition', ...mk() },
        { id: 'coding', type: 'place', vars: { visible: true, token: true } },
      ],
      edges: [
        { from: 'A', to: 't', type: 'next' },
        { from: 't', to: 'coding', type: 'token' },
      ],
    };
    // t a 1 in (A) / 1 out (coding=pot) → pas clippable (voisin pot).
    const clips = findClippable(g);
    expect(clips.some((c) => c.type === 'seq' && c.on.includes('t'))).toBe(false);
  });

  it('zip_all / unzip_all : retour à l\'état initial', () => {
    const g: any = {
      nodes: [
        { id: 'A', type: 'place', vars: { visible: true } },
        { id: 't1', type: 'transition', vars: { visible: true } },
        { id: 'P', type: 'place', vars: { visible: true } },
        { id: 't2', type: 'transition', vars: { visible: true } },
        { id: 'B', type: 'place', vars: { visible: true } },
      ],
      edges: [
        { from: 'A', to: 't1', type: 'next' },
        { from: 't1', to: 'P', type: 'next' },
        { from: 'P', to: 't2', type: 'next' },
        { from: 't2', to: 'B', type: 'next' },
      ],
    };
    const before = JSON.stringify(g.nodes.map((n: any) => n.id).sort());
    const table = zipAll(g);
    expect(table.clips.length).toBeGreaterThan(0);
    unzipAll(g, table);
    expect(g.nodes.filter((n: any) => n.id.startsWith('z:')).length).toBe(0);
    expect(JSON.stringify(g.nodes.map((n: any) => n.id).sort())).toBe(before);
    expect(g.nodes.find((n: any) => n.id === 'P').vars.visible).toBe(true);
  });
});

describe('taskflowClip — état maintenu + lazy single', () => {
  it('rebuildState : single en O(1) via innerPlacesVisible (pas de rescan inner)', () => {
    const g: any = {
      nodes: [
        { id: 'box', type: 'flow', vars: { visible: true, inner: { nodes: [
          { id: 'p1', type: 'place', vars: { visible: true } },
          { id: 'ti', type: 'transition', vars: { visible: true } },
        ], edges: [] } } },
      ],
      edges: [],
    };
    rebuildState(g);
    expect(g.nodes[0].vars.innerPlacesVisible).toBe(1);
    expect(g.nodes[0].vars.zippable).toBe('single');
    // La place interne devient invisible (maintenu en lazy) → plus single.
    g.nodes[0].vars.inner.nodes[0].vars.visible = false;
    g.nodes[0].vars.innerPlacesVisible = 0;
    recheckNode(g, 'box');
    expect(g.nodes[0].vars.zippable).toBe('none');
  });

  it('recheckNode : la pureté suit les longueurs de groupes (out_group/in_group)', () => {
    const g: any = {
      nodes: [
        { id: 'A', type: 'place', vars: { visible: true } },
        { id: 't1', type: 'transition', vars: { visible: true } },
        { id: 'P', type: 'place', vars: { visible: true } },
        { id: 't2', type: 'transition', vars: { visible: true } },
        { id: 'B', type: 'place', vars: { visible: true } },
      ],
      edges: [
        { from: 'A', to: 't1' }, { from: 't1', to: 'P' },
        { from: 'P', to: 't2' }, { from: 't2', to: 'B' },
      ],
    };
    rebuildState(g);
    expect(g.nodes.find((n: any) => n.id === 'P').vars.zippable).toBe('seq');
    // t1 gagne un 2e voisin de sortie → x.out_group=2 → P n'est plus seq.
    const t1 = g.nodes.find((n: any) => n.id === 't1');
    t1.vars.out_group = ['P', 'X'];
    recheckNode(g, 'P');
    expect(g.nodes.find((n: any) => n.id === 'P').vars.zippable).toBe('none');
    // Retour à 1 voisin → P redevient seq.
    t1.vars.out_group = ['P'];
    recheckNode(g, 'P');
    expect(g.nodes.find((n: any) => n.id === 'P').vars.zippable).toBe('seq');
  });

  it('zip single : masque la place dans l\'inner, crée z dans l\'inner → box affichée (z visible), unzip restaure', () => {
    const g: any = {
      nodes: [
        { id: 'box', type: 'flow', vars: { visible: true, inner: { nodes: [
          { id: 'p1', type: 'place', vars: { visible: true } },
          { id: 'ti', type: 'transition', vars: { visible: true } },
        ], edges: [{ from: 'p1', to: 'ti', type: 'next' }] } } },
        { id: 'B', type: 'place', vars: { visible: true } },
      ],
      edges: [{ from: 'box', to: 'B', type: 'next' }],
    };
    const clips = findClippable(g);
    const single = clips.find((c) => c.type === 'single');
    expect(single).toBeTruthy();
    zip(g, single!);
    const inner = g.nodes.find((n: any) => n.id === 'box').vars.inner;
    expect(inner.nodes.find((n: any) => n.id === 'p1').vars.visible).toBe(false);
    expect(inner.nodes.some((n: any) => n.id === single!.newId)).toBe(true);
    expect(g.nodes.find((n: any) => n.id === 'box').vars.innerPlacesVisible).toBe(0);
    // Règle show : la box s'affiche tant qu'elle a ≥1 NŒUD visible (z) ; plus
    // aucune place interne visible n'affiche pas la box (fallback hors taskflow).
    const pruned = pruneInvisible(g);
    expect(pruned.nodes.some((n: any) => n.id === 'box')).toBe(true);
    expect(g.nodes.find((n: any) => n.id === 'box').vars.visible).toBe(true);
    // unzip : restaure p1, supprime z, compteur 1.
    expect(unzip(g, single!)).toBe(true);
    expect(inner.nodes.find((n: any) => n.id === 'p1').vars.visible).toBe(true);
    expect(inner.nodes.some((n: any) => n.id === single!.newId)).toBe(false);
    expect(g.nodes.find((n: any) => n.id === 'box').vars.innerPlacesVisible).toBe(1);
  });

  it('zip seq dans un box : le compteur lazy est décrémenté (places zippées)', () => {
    const g: any = {
      nodes: [
        { id: 'box', type: 'flow', vars: { visible: true, inner: { nodes: [
          { id: 'A', type: 'place', vars: { visible: true } },
          { id: 't1', type: 'transition', vars: { visible: true } },
          { id: 'P', type: 'place', vars: { visible: true } },
          { id: 't2', type: 'transition', vars: { visible: true } },
          { id: 'B', type: 'place', vars: { visible: true } },
        ], edges: [
          { from: 'A', to: 't1', type: 'next' }, { from: 't1', to: 'P', type: 'next' },
          { from: 'P', to: 't2', type: 'next' }, { from: 't2', to: 'B', type: 'next' },
        ] } } },
      ],
      edges: [],
    };
    const clips = findClippable(g);
    const seq = clips.find((c) => c.type === 'seq' && c.on.includes('P'));
    expect(seq).toBeTruthy();
    expect(seq!.path).toEqual(['box']);
    // 3 places visibles dans le box (A, P, B) ; zip de P → 2.
    expect(g.nodes.find((n: any) => n.id === 'box').vars.innerPlacesVisible).toBe(3);
    zip(g, seq!);
    expect(g.nodes.find((n: any) => n.id === 'box').vars.innerPlacesVisible).toBe(2);
  });
});

describe('taskflowClip — box transparentes (modèle utilisateur)', () => {
  // A → tA → [box B: B/in → tB → B/out] → tB2 → C
  function mkContractGraph() {
    return {
      nodes: [
        { id: 'A', type: 'place', vars: { visible: true } },
        { id: 'tA', type: 'transition', vars: { visible: true } },
        { id: 'B', type: 'skill', vars: { visible: true, inner: {
          nodes: [
            { id: 'B/in', type: 'skill_input', vars: { visible: true } },
            { id: 'tB', type: 'transition', vars: { visible: true } },
            { id: 'B/out', type: 'skill_output', vars: { visible: true } },
          ],
          edges: [{ from: 'B/in', to: 'tB', type: 'next' }, { from: 'tB', to: 'B/out', type: 'next' }],
          entrypoint: 'B/in', exitpoints: ['B/out'],
        } } },
        { id: 'tB2', type: 'transition', vars: { visible: true } },
        { id: 'C', type: 'place', vars: { visible: true } },
      ],
      edges: [
        { from: 'A', to: 'tA', type: 'next' },
        { from: 'tA', to: 'B', type: 'next' },
        { from: 'B', to: 'tB2', type: 'next' },
        { from: 'tB2', to: 'C', type: 'next' },
      ],
    };
  }

  it('les nœuds input/transition/output d\'un contrat sont foldables (routing)', () => {
    const g: any = mkContractGraph();
    rebuildState(g);
    const inNode = g.nodes.find((n: any) => n.id === 'B').vars.inner.nodes.find((n: any) => n.id === 'B/in');
    const outNode = g.nodes.find((n: any) => n.id === 'B').vars.inner.nodes.find((n: any) => n.id === 'B/out');
    const tB = g.nodes.find((n: any) => n.id === 'B').vars.inner.nodes.find((n: any) => n.id === 'tB');
    // Routing : l'arête externe tA→B entre par B/in, B→tB2 sort par B/out.
    expect(inNode.vars.in_group).toEqual(['tA']);
    expect(outNode.vars.out_group).toEqual(['tB2']);
    // Les 3 nœuds du contrat sont seq.
    expect(inNode.vars.zippable).toBe('seq');
    expect(tB.vars.zippable).toBe('seq');
    expect(outNode.vars.zippable).toBe('seq');
    // Les transitions ADJACENTES aux boxes (tA, tB2) sont aussi seq : le
    // routing les branche sur les PLACES (B/in, B/out), pas sur la box.
    const tA = g.nodes.find((n: any) => n.id === 'tA');
    const tB2 = g.nodes.find((n: any) => n.id === 'tB2');
    expect(tA.vars.zippable).toBe('seq');
    expect(tB2.vars.zippable).toBe('seq');
  });

  it('zip d\'un contrat trans-frontière → z DANS la box (tous les zippés y sont), box affichée (z visible), unzip restaure', () => {
    const g: any = mkContractGraph();
    const clips = findClippable(g);
    const tB = clips.find((c: any) => c.type === 'seq' && c.on.includes('tB'));
    expect(tB).toBeTruthy();
    zip(g, tB!);
    // z créé DANS la box B (tous les zippés B/in,tB,B/out y sont) — pas au top.
    const inner = g.nodes.find((n: any) => n.id === 'B').vars.inner;
    const z = inner.nodes.find((n: any) => n.id === tB!.newId);
    expect(z).toBeTruthy();
    expect(z!.vars.visible).toBe(true);
    expect(g.nodes.find((n: any) => n.id === tB!.newId)).toBeUndefined();
    // Arêtes trans-frontières : tA→z (dans l'inner de B), z→tB2 (au top).
    expect(inner.edges.some((e: any) => e.from === 'tA' && e.to === tB!.newId)).toBe(true);
    expect(g.edges.some((e: any) => e.from === tB!.newId && e.to === 'tB2')).toBe(true);
    // La box N'EST PAS masquée (visible true) : elle s'affiche car z est visible.
    // z est une PLACE (fold d'une transition → place) → innerPlacesVisible=1.
    expect(g.nodes.find((n: any) => n.id === 'B').vars.visible).toBe(true);
    expect(g.nodes.find((n: any) => n.id === 'B').vars.innerVisibleNodes).toBe(1);
    expect(g.nodes.find((n: any) => n.id === 'B').vars.innerPlacesVisible).toBe(1);
    // Les zippés sont invisibles.
    expect(inner.nodes.find((n: any) => n.id === 'B/in').vars.visible).toBe(false);
    expect(inner.nodes.find((n: any) => n.id === 'B/out').vars.visible).toBe(false);
    expect(inner.nodes.find((n: any) => n.id === 'tB').vars.visible).toBe(false);
    // unzip : tout restauré.
    expect(unzip(g, tB!)).toBe(true);
    expect(g.nodes.some((n: any) => n.id === tB!.newId)).toBe(false);
    expect(inner.nodes.find((n: any) => n.id === 'B/in').vars.visible).toBe(true);
    expect(inner.nodes.find((n: any) => n.id === 'tB').vars.visible).toBe(true);
    expect(inner.nodes.find((n: any) => n.id === 'B/out').vars.visible).toBe(true);
  });
});

describe('taskflowClip — type du nœud généré (alternance SÉQUENTIEL)', () => {
  it('seq sur une PLACE → génère une transition ; seq sur une TRANSITION → génère une place', () => {
    // A → t1 → P → t2 → B  (tous feuilles)
    const g: any = {
      nodes: [
        { id: 'A', type: 'place', vars: { visible: true } },
        { id: 't1', type: 'transition', vars: { visible: true } },
        { id: 'P', type: 'place', vars: { visible: true } },
        { id: 't2', type: 'transition', vars: { visible: true } },
        { id: 'B', type: 'place', vars: { visible: true } },
      ],
      edges: [
        { from: 'A', to: 't1' }, { from: 't1', to: 'P' },
        { from: 'P', to: 't2' }, { from: 't2', to: 'B' },
      ],
    };
    const clips = findClippable(g);
    // fold sur P (place) → z est une TRANSITION.
    const cP = clips.find((c: any) => c.type === 'seq' && c.on.includes('P'));
    zip(g, cP!);
    expect(g.nodes.find((n: any) => n.id === cP!.newId)?.type).toBe('transition');
    unzip(g, cP!);
    // fold sur t1 (transition) → z est une PLACE.
    const clips2 = findClippable(g);
    const cT = clips2.find((c: any) => c.type === 'seq' && c.on.includes('t1'));
    expect(cT).toBeTruthy();
    zip(g, cT!);
    expect(g.nodes.find((n: any) => n.id === cT!.newId)?.type).toBe('place');
    // unzip restaure (P et t1 reviennent à l'état initial).
    expect(unzip(g, cT!)).toBe(true);
    expect(g.nodes.find((n: any) => n.id === 'P').vars.visible).toBe(true);
    expect(g.nodes.find((n: any) => n.id === 't1').vars.visible).toBe(true);
  });
});
