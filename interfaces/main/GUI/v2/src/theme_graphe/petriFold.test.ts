import { describe, it, expect } from 'vitest';
import {
  makeNet, fsmToPetri, fold, reduceLinear, reduceCycles,
  petriToGraph, prePlaces, postPlaces, findSeqFolds, findParFolds,
  findFolds, findFoldsRec, applyFoldOnce, applyFolds, autoFold,
  type PetriNet, type Fold,
} from '../theme_graphe/petriFold.ts';

// ── Aides ───────────────────────────────────────────────────────────

function nodesOf(net: PetriNet, kind: string): string[] {
  return net.nodes.filter((n) => n.kind === kind).map((n) => n.id);
}

// ── Conversion FSM → Pétri ──────────────────────────────────────────

describe('fsmToPetri — FSM → réseau de Pétri', () => {
  it('chaque nœud FSM devient une place, chaque arête une transition', () => {
    const net = fsmToPetri({
      nodes: [
        { id: 'pick', type: 'skill', label: 'pick' },
        { id: 'git', type: 'skill', label: 'git' },
        { id: 'end', type: 'exitpoint', label: 'end' },
      ],
      edges: [
        { from: 'pick', to: 'git' },
        { from: 'git', to: 'end' },
      ],
    }, 'pick');
    // 3 places (nœuds FSM) + 2 transitions (arêtes).
    expect(nodesOf(net, 'place')).toEqual(expect.arrayContaining(['pick', 'git', 'end']));
    expect(nodesOf(net, 'transition')).toEqual(
      expect.arrayContaining(['t:pick->git', 't:git->end']));
    // La transition pick→git relie la place pick (pré) à la place git (post).
    expect(prePlaces(net, 't:pick->git')).toEqual(['pick']);
    expect(postPlaces(net, 't:pick->git')).toEqual(['git']);
  });
});

// ── Réduction de SÉQUENCE LINÉAIRE ─────────────────────────────────

describe('reduceLinear — séquence linéaire', () => {
  // A →(tB)→ C →(tD)→ E  (aucun autre flux) → A →(TX)→ E
  it('fusionne une place à flux unique en macro-transition', () => {
    const net = makeNet(
      [
        { id: 'A', kind: 'place', label: 'A', meta: 'entry' },
        { id: 'C', kind: 'place', label: 'C' },
        { id: 'E', kind: 'place', label: 'E' },
        { id: 'tB', kind: 'transition', label: 'B' },
        { id: 'tD', kind: 'transition', label: 'D' },
      ],
      [
        { from: 'A', to: 'tB' },
        { from: 'tB', to: 'C' },
        { from: 'C', to: 'tD' },
        { from: 'tD', to: 'E' },
      ],
    );
    const r = reduceLinear(net);
    // La place C a disparu ; tB et tD fusionnés en une macro-transition x:tB+tD.
    expect(r.nodes.map((n) => n.id)).not.toContain('C');
    expect(r.nodes.filter((n) => n.meta === 'boxed').length).toBe(1);
    const box = r.nodes.find((n) => n.meta === 'boxed')!;
    expect(prePlaces(r, box.id)).toEqual(['A']);
    expect(postPlaces(r, box.id)).toEqual(['E']);
    // Le détail T1→P→T2 est conservé dans la box.
    expect(box.inner!.nodes.map((n) => n.id)).toEqual(['tB', 'C', 'tD']);
    expect(box.inner!.arcs.map((a) => `${a.from}->${a.to}`)).toEqual(['tB->C', 'C->tD']);
  });

  it('ne touche pas une place partagée (2 flux entrants)', () => {
    // A→tB→C, D→tE→C, C→tF→G : C a 2 entrées → pas de fusion.
    const net = makeNet(
      [
        { id: 'A', kind: 'place', label: 'A', meta: 'entry' },
        { id: 'C', kind: 'place', label: 'C' },
        { id: 'D', kind: 'place', label: 'D' },
        { id: 'G', kind: 'place', label: 'G' },
        { id: 'tB', kind: 'transition', label: 'B' },
        { id: 'tE', kind: 'transition', label: 'E' },
        { id: 'tF', kind: 'transition', label: 'F' },
      ],
      [
        { from: 'A', to: 'tB' }, { from: 'tB', to: 'C' },
        { from: 'D', to: 'tE' }, { from: 'tE', to: 'C' },
        { from: 'C', to: 'tF' }, { from: 'tF', to: 'G' },
      ],
    );
    const r = reduceLinear(net);
    expect(r.nodes.find((n) => n.id === 'C')).toBeTruthy(); // pas fusionnée
    expect(r.nodes.filter((n) => n.meta === 'boxed').length).toBe(0);
  });
});

// ── Folding interactif : règles seq et par ─────────────────────────

describe('folding interactif — règles seq/par', () => {
  it('règle seq : xAy → z (z = A, x et y = transitions absorbées)', () => {
    const doc = {
      nodes: [
        { id: 'x', type: 'place', label: 'x', vars: {} },
        { id: 'tIn', type: 'transition', label: '', vars: {} },
        { id: 'A', type: 'place', label: 'A', vars: {} },
        { id: 'tOut', type: 'transition', label: '', vars: {} },
        { id: 'y', type: 'place', label: 'y', vars: {} },
      ],
      edges: [
        { from: 'x', to: 'tIn', type: 'next' },
        { from: 'tIn', to: 'A', type: 'next' },
        { from: 'A', to: 'tOut', type: 'next' },
        { from: 'tOut', to: 'y', type: 'next' },
      ],
    };
    const folds = findSeqFolds(doc);
    expect(folds.length).toBe(1);
    const f = folds[0];
    expect(f.rule).toBe('seq');
    expect(f.label).toBe('A');          // z s'appelle A
    expect(f.center).toBe('A');
    expect(f.removed).toEqual(['tIn', 'A', 'tOut']);
    // Fold : tIn, A, tOut retirés ; x et y RESTENT ; z ajouté.
    const d2 = applyFoldOnce(doc, f);
    expect(d2.nodes.some((n: any) => n.id === f.zId)).toBe(true);
    expect(d2.nodes.some((n: any) => n.id === 'A')).toBe(false);
    expect(d2.nodes.some((n: any) => n.id === 'tIn')).toBe(false);
    expect(d2.nodes.some((n: any) => n.id === 'x')).toBe(true);   // x reste
    expect(d2.nodes.some((n: any) => n.id === 'y')).toBe(true);   // y reste
    // Les arêtes x→z et z→y.
    expect(d2.edges.some((e: any) => e.from === 'x' && e.to === f.zId)).toBe(true);
    expect(d2.edges.some((e: any) => e.from === f.zId && e.to === 'y')).toBe(true);
    // Le contenu (x→tIn→A→tOut→y est représenté par z = [tIn, A, tOut]).
    expect(f.inner.nodes.length).toBe(3);
  });

  it('règle par : groupe de transitions à mêmes entrées/sorties → AzB (z = x)', () => {
    const doc = {
      nodes: [
        { id: 'A', type: 'place', label: 'A', vars: {} },
        { id: 'C', type: 'place', label: 'C', vars: {} },
        { id: 't1', type: 'transition', label: 'x', vars: {} },
        { id: 't2', type: 'transition', label: 'y', vars: {} },
        { id: 'B', type: 'place', label: 'B', vars: {} },
        { id: 'D', type: 'place', label: 'D', vars: {} },
      ],
      edges: [
        { from: 'A', to: 't1', type: 'next' },
        { from: 'C', to: 't1', type: 'next' },
        { from: 't1', to: 'B', type: 'next' },
        { from: 't1', to: 'D', type: 'next' },
        { from: 'A', to: 't2', type: 'next' },
        { from: 'C', to: 't2', type: 'next' },
        { from: 't2', to: 'B', type: 'next' },
        { from: 't2', to: 'D', type: 'next' },
      ],
    };
    const folds = findParFolds(doc);
    expect(folds.length).toBe(1);
    const f = folds[0];
    expect(f.rule).toBe('par');
    expect(f.label).toBe('x');          // z s'appelle x (une des transitions)
    expect(f.removed).toEqual(expect.arrayContaining(['t1', 't2']));
    const d2 = applyFoldOnce(doc, f);
    // t1, t2 retirés ; z ajouté ; les arêtes rebranchées sur z (dédupliquées).
    expect(d2.nodes.some((n: any) => n.id === f.zId)).toBe(true);
    expect(d2.nodes.some((n: any) => n.id === 't1')).toBe(false);
    expect(d2.nodes.some((n: any) => n.id === 't2')).toBe(false);
    // z a les mêmes in/out : A→z, C→z, z→B, z→D (une seule fois chacun).
    expect(d2.edges.filter((e: any) => e.to === f.zId).map((e: any) => e.from).sort()).toEqual(['A', 'C']);
    expect(d2.edges.filter((e: any) => e.from === f.zId).map((e: any) => e.to).sort()).toEqual(['B', 'D']);
    // Le contenu (t1 ∥ t2) est conservé dans z.
    expect(f.inner.nodes.map((n: any) => n.id)).toEqual(expect.arrayContaining(['t1', 't2', 'A', 'B']));
  });

  it('applyFolds + autoFold : plie de l\'entrée vers la sortie', () => {
    const doc = {
      nodes: [
        { id: 'main', type: 'place', label: 'main', vars: {} },
        { id: 't1', type: 'transition', label: '', vars: {} },
        { id: 'A', type: 'place', label: 'A', vars: {} },
        { id: 't2', type: 'transition', label: '', vars: {} },
        { id: 'B', type: 'place', label: 'B', vars: {} },
        { id: 't3', type: 'transition', label: '', vars: {} },
        { id: 'end', type: 'place', label: 'end', vars: {} },
      ],
      edges: [
        { from: 'main', to: 't1', type: 'next' },
        { from: 't1', to: 'A', type: 'next' },
        { from: 'A', to: 't2', type: 'next' },
        { from: 't2', to: 'B', type: 'next' },
        { from: 'B', to: 't3', type: 'next' },
        { from: 't3', to: 'end', type: 'next' },
      ],
    };
    const { doc: folded, foldState } = autoFold(doc);
    // Tout le chemin linéaire est plié : il reste main, end, et des z.
    expect(folded.nodes.some((n: any) => n.id === 'A')).toBe(false);
    expect(folded.nodes.some((n: any) => n.id === 'B')).toBe(false);
    expect(foldState.size).toBeGreaterThan(0);
    // Unfold complet → retour au doc initial.
    const unfolded = applyFolds(doc, new Map());
    expect(unfolded.nodes.map((n: any) => n.id)).toEqual(doc.nodes.map((n: any) => n.id));
  });

  it('folding RÉCURSIF dans les sous-graphes (vars.inner)', () => {
    const doc = {
      nodes: [
        { id: 'step', type: 'petri-box', label: 'step', vars: { inner: {
          nodes: [
            { id: 'x', type: 'place', label: 'x', vars: {} },
            { id: 'tIn', type: 'transition', label: '', vars: {} },
            { id: 'A', type: 'place', label: 'A', vars: {} },
            { id: 'tOut', type: 'transition', label: '', vars: {} },
            { id: 'y', type: 'place', label: 'y', vars: {} },
          ],
          edges: [
            { from: 'x', to: 'tIn', type: 'next' },
            { from: 'tIn', to: 'A', type: 'next' },
            { from: 'A', to: 'tOut', type: 'next' },
            { from: 'tOut', to: 'y', type: 'next' },
          ],
        } } },
      ],
      edges: [],
    };
    // findFolds (récursif) détecte le fold du sous-graphe avec son chemin.
    const folds = findFolds(doc);
    const sub = folds.find((f) => f.rule === 'seq' && f.center === 'A');
    expect(sub).toBeTruthy();
    expect(sub!.path).toEqual(['step']);
    // L'application se fait dans le vars.inner du step.
    const d2 = applyFolds(doc, new Map([[sub!.zId, sub!]]));
    const inner = (d2.nodes.find((n: any) => n.id === 'step') as any).vars.inner;
    expect(inner.nodes.some((n: any) => n.id === 'A')).toBe(false);
    expect(inner.nodes.some((n: any) => n.id === sub!.zId)).toBe(true);
    expect(inner.edges.some((e: any) => e.from === 'x' && e.to === sub!.zId)).toBe(true);
  });
});

describe('reduceCycles — boucle', () => {
  // pA→tB→pC→tD→pE→tF→pG→tH→pA : cycle complet → boxé en TX.
  it('boxe un SCC cyclique en macro-transition (boucle interne conservée)', () => {
    const net = makeNet(
      [
        { id: 'pA', kind: 'place', label: 'A', meta: 'entry' },
        { id: 'pC', kind: 'place', label: 'C' },
        { id: 'pE', kind: 'place', label: 'E' },
        { id: 'pG', kind: 'place', label: 'G' },
        { id: 'tB', kind: 'transition', label: 'B' },
        { id: 'tD', kind: 'transition', label: 'D' },
        { id: 'tF', kind: 'transition', label: 'F' },
        { id: 'tH', kind: 'transition', label: 'H' },
      ],
      [
        { from: 'pA', to: 'tB' }, { from: 'tB', to: 'pC' },
        { from: 'pC', to: 'tD' }, { from: 'tD', to: 'pE' },
        { from: 'pE', to: 'tF' }, { from: 'tF', to: 'pG' },
        { from: 'pG', to: 'tH' }, { from: 'tH', to: 'pA' },  // retour → boucle
      ],
    );
    const r = reduceCycles(net);
    // Tout le cycle est boxé : il reste 1 transition (macro) + les places du
    // SCC qui étaient frontières restent… le SCC complet devient une macro.
    expect(r.nodes.filter((n) => n.meta === 'boxed').length).toBe(1);
    const box = r.nodes.find((n) => n.meta === 'boxed')!;
    // La boucle interne est conservée dans la box.
    expect(box.inner!.arcs.some((a) => a.from === 'tH' && a.to === 'pA')).toBe(true);
    expect(box.inner!.arcs.some((a) => a.from === 'tB' && a.to === 'pC')).toBe(true);
  });
});

// ── Pipeline complet + sortie hiérarchique ─────────────────────────

describe('fold + petriToGraph', () => {
  it('produit un graphe hiérarchique boxable (linéaire réduit)', () => {
    const net = fsmToPetri({
      nodes: [
        { id: 'pick', type: 'skill', label: 'pick' },
        { id: 'clone', type: 'skill', label: 'clone' },
        { id: 'work', type: 'flow', label: 'work' },
        { id: 'end', type: 'exitpoint', label: 'end' },
      ],
      edges: [
        { from: 'pick', to: 'clone' },
        { from: 'clone', to: 'work' },
        { from: 'work', to: 'end' },
      ],
    }, 'pick');
    const folded = fold(net);
    const g = petriToGraph(folded);
    // La chaîne 100% linéaire pick→clone→work→end est réduite en UNE box
    // (macro-transition), avec la place source qui reste : p:pick → box.
    const boxes = g.nodes.filter((n: any) => n.type === 'petri-box');
    expect(boxes.length).toBe(1);
    expect(g.nodes.some((n: any) => n.type === 'place')).toBe(true);  // source
    expect(g.nodes.filter((n: any) => n.type === 'transition').length).toBe(0);
    // La box est dépliable : son vars.inner contient le détail pick→clone→work→end.
    const box = boxes[0];
    expect(box.vars.inner.nodes.length).toBeGreaterThan(3);
    expect(box.vars.inner.edges.length).toBeGreaterThan(0);
  });
});
