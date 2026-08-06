import { describe, it, expect } from 'vitest';
import type { Layout } from '../layout/types.ts';
import {
  addPanel, closeTab, activateTab, moveTab, splitGroup, reorderTabs,
  addMiniLayout, validateLayout, findGroup, findPanelOccurrence, listPresentPanels, paramsEqual,
  resizeSplit,
} from '../layout/ops.ts';

/** Layout de test : un split horizontal avec 2 groupes. */
function baseLayout(): Layout {
  return {
    id: 'test',
    tree: {
      type: 'split', id: 'sp-test', direction: 'horizontal',
      children: [
        { type: 'group', id: 'pg-A', tabs: [{ panel: 'ressources', occId: 'occ-1' }], active: 'occ-1' },
        { type: 'group', id: 'pg-B', tabs: [{ panel: 'etat', occId: 'occ-2' }], active: 'occ-2' },
      ],
    },
  };
}

describe('invariants de base', () => {
  it('le layout de base est valide', () => {
    expect(validateLayout(baseLayout())).toEqual([]);
  });

  it('chaque groupe a au moins 1 onglet', () => {
    const l = baseLayout();
    expect(validateLayout(l)).toEqual([]);
  });

  it('les occId sont uniques dans l\'arbre', () => {
    const l = baseLayout();
    expect(validateLayout(l)).toEqual([]);
  });
});

describe('addPanel', () => {
  it('ajoute un onglet dans un groupe existant', () => {
    const l = addPanel(baseLayout(), 'pg-A', 'chat', { canal: 'dev' });
    expect(validateLayout(l)).toEqual([]);
    // le nouveau panel est dans pg-A
    const group = findGroup(l.tree, 'occ-1')!;
    expect(group.id).toBe('pg-A');
    expect(group.tabs.some((t) => t.panel === 'chat')).toBe(true);
  });

  it('crée un groupe si le groupe cible n\'existe pas', () => {
    const l = addPanel(baseLayout(), 'pg-inexistant', 'chat');
    expect(validateLayout(l)).toEqual([]);
  });

  it('le panel est toujours dans un onglet (jamais nu)', () => {
    const l = addPanel(baseLayout(), 'pg-A', 'chat');
    // tout occId est dans un groupe
    expect(validateLayout(l)).toEqual([]);
  });
});

describe('unicité d\'un panel par fenêtre (params identiques)', () => {
  it('addPanel ne duplique pas un panel déjà présent sans params', () => {
    let l = addPanel(baseLayout(), 'pg-A', 'chat');
    l = addPanel(l, 'pg-A', 'chat');
    // toujours un seul 'chat'
    const occs = listPresentPanels(l.tree).filter((o) => o.panel === 'chat');
    expect(occs).toHaveLength(1);
    expect(validateLayout(l)).toEqual([]);
  });

  it('addPanel ne duplique pas un panel présent avec les mêmes params', () => {
    let l = addPanel(baseLayout(), 'pg-A', 'chat', { canal: 'dev', mode: 'a' });
    l = addPanel(l, 'pg-A', 'chat', { mode: 'a', canal: 'dev' }); // ordre de clés différent
    const occs = listPresentPanels(l.tree).filter((o) => o.panel === 'chat');
    expect(occs).toHaveLength(1);
  });

  it('addPanel autorise un 2e exemplaire si les params diffèrent', () => {
    let l = addPanel(baseLayout(), 'pg-A', 'chat', { canal: 'dev' });
    l = addPanel(l, 'pg-A', 'chat', { canal: 'prod' });
    const occs = listPresentPanels(l.tree).filter((o) => o.panel === 'chat');
    expect(occs).toHaveLength(2);
    expect(validateLayout(l)).toEqual([]);
  });

  it('l\'exemplaire déjà présent est activé (juste visible)', () => {
    let l = addPanel(baseLayout(), 'pg-A', 'chat');
    const first = findPanelOccurrence(l.tree, 'chat')!;
    // un 2e panel dans pg-A pour que l'activation soit observable
    l = addPanel(l, 'pg-A', 'ide');
    l = addPanel(l, 'pg-A', 'chat'); // ré-ajout → active l'existant, pas de nouveau
    const occs = listPresentPanels(l.tree).filter((o) => o.panel === 'chat');
    expect(occs).toHaveLength(1);
    const group = findGroup(l.tree, first.occ.occId)!;
    expect(group.active).toBe(first.occ.occId);
  });

  it('la recherche traverse les slip views (sous-layout)', () => {
    let l = addMiniLayout(baseLayout(), 'pg-A', 'chat');
    expect(findPanelOccurrence(l.tree, 'chat')).not.toBeNull();
    // addMiniLayout en double → skip
    const before = JSON.stringify(l);
    l = addMiniLayout(l, 'pg-A', 'chat');
    expect(JSON.stringify(l)).toBe(before);
  });

  it('paramsEqual ignore l\'ordre des clés', () => {
    expect(paramsEqual({ a: 1, b: [1, 2] }, { b: [1, 2], a: 1 })).toBe(true);
    expect(paramsEqual({ a: 1 }, undefined)).toBe(false);
    expect(paramsEqual(undefined, undefined)).toBe(true);
    expect(paramsEqual(undefined, {})).toBe(true);
  });
});

describe('closeTab', () => {
  it('ferme un onglet (le groupe garde ≥1 onglet)', () => {
    const l = closeTab(baseLayout(), 'pg-A', 'occ-1');
    // pg-A devient vide → supprimé → il reste pg-B
    expect(validateLayout(l)).toEqual([]);
  });

  it('ne laisse jamais un groupe vide', () => {
    // ajoute un 2e onglet, ferme le 1er → le groupe reste
    let l = addPanel(baseLayout(), 'pg-A', 'chat');
    const occ1 = findGroup(l.tree, 'occ-1')!.tabs.find((t) => t.occId === 'occ-1')!.occId;
    l = closeTab(l, 'pg-A', occ1);
    expect(validateLayout(l)).toEqual([]);
  });
});

describe('activateTab', () => {
  it('change l\'onglet actif', () => {
    let l = addPanel(baseLayout(), 'pg-A', 'chat');
    const occChat = findGroup(l.tree, 'occ-1')!.tabs.find((t) => t.panel === 'chat')!.occId;
    l = activateTab(l, 'pg-A', occChat);
    const group = findGroup(l.tree, occChat)!;
    expect(group.active).toBe(occChat);
  });
});

describe('moveTab', () => {
  it('déplace un onglet d\'un groupe à un autre', () => {
    const l = moveTab(baseLayout(), 'pg-A', 'pg-B', 'occ-1');
    expect(validateLayout(l)).toEqual([]);
    // occ-1 est maintenant dans pg-B
    const g = findGroup(l.tree, 'occ-1')!;
    expect(g.id).toBe('pg-B');
  });

  it('déplace cross-group à un index précis', () => {
    // baseLayout : pg-A=[occ-1], pg-B=[occ-2]. Ajoutons un onglet à pg-B.
    let l = addPanel(baseLayout(), 'pg-B', 'chat');
    // occ-chat ajouté à pg-B → [occ-2, chat]
    const b0 = findGroup(l.tree, 'occ-2')!;
    expect(b0.tabs.map((t) => t.panel)).toContain('chat');
    // déplace occ-1 (pg-A) dans pg-B à l'index 0
    l = moveTab(l, 'pg-A', 'pg-B', 'occ-1', 0);
    expect(validateLayout(l)).toEqual([]);
    const b = findGroup(l.tree, 'occ-1')!;
    const tabs = b.tabs.map((t) => t.occId);
    expect(tabs[0]).toBe('occ-1');
    expect(b.id).toBe('pg-B');
  });
});

describe('splitGroup', () => {
  it('split un onglet dans une nouvelle zone', () => {
    const l = splitGroup(baseLayout(), 'pg-B', 'vertical', 'occ-1');
    expect(validateLayout(l)).toEqual([]);
    // occ-1 existe toujours
    expect(findGroup(l.tree, 'occ-1')).not.toBeNull();
  });

  it('split cross-group : insère le nouvel onglet à côté du groupe cible', () => {
    // pg-A=[occ-1], pg-B=[occ-2]. Split occ-1 à côté de pg-B.
    const l = splitGroup(baseLayout(), 'pg-B', 'horizontal', 'occ-1', 'pg-A');
    expect(validateLayout(l)).toEqual([]);
    // occ-1 dans un groupe séparé, adjacent à pg-B
    const g1 = findGroup(l.tree, 'occ-1')!;
    const g2 = findGroup(l.tree, 'occ-2')!;
    expect(g1.id).not.toBe(g2.id);
    // pg-A a été vidé → occ-1 a bougé
    expect(g1.tabs.length).toBe(1);
  });
});

describe('reorderTabs', () => {
  it('réordonne les onglets d\'un groupe', () => {
    let l = addPanel(baseLayout(), 'pg-A', 'chat');
    const group = findGroup(l.tree, 'occ-1')!;
    const order = [...group.tabs.map((t) => t.occId)].reverse();
    l = reorderTabs(l, 'pg-A', order);
    const after = findGroup(l.tree, 'occ-1')!;
    expect(after.tabs.map((t) => t.occId)).toEqual(order);
  });
});

describe('addMiniLayout', () => {
  it('ajoute une slip view (sous-arbre à 1 onglet)', () => {
    const l = addMiniLayout(baseLayout(), 'pg-A', 'ide');
    expect(validateLayout(l)).toEqual([]);
  });
});

describe('resizeSplit', () => {
  it('redimensionne les enfants d\'un split (somme conservée)', () => {
    const l = resizeSplit(baseLayout(), 'sp-test', 0, 20, 1000);
    // sizes: [50+2, 50-2] = [52, 48]
    expect(l.tree.type).toBe('split');
    const sp = l.tree as any;
    expect(sp.sizes).toEqual([52, 48]);
    expect(sp.sizes[0] + sp.sizes[1]).toBeCloseTo(100);
    expect(validateLayout(l)).toEqual([]);
  });

  it('borne le resize pour ne pas écraser un enfant', () => {
    const l = resizeSplit(baseLayout(), 'sp-test', 0, 1000, 1000);
    const sp = l.tree as any;
    expect(sp.sizes[0]).toBe(95); // plancher 5%
    expect(sp.sizes[1]).toBe(5);
  });

  it('applique des deltas INCÉRÉMENTAUX sans déraper (pas de double-compte)', () => {
    // Chaque mousemove passe le delta depuis le DERNIER événement. Appliquer
    // 3 deltas de +2% chacun → +6% au total (PAS +2% × 3 ré-appliqués depuis
    // zéro = dérapage).
    let l = baseLayout();
    for (let i = 0; i < 3; i++) {
      l = resizeSplit(l, 'sp-test', 0, 2, 100); // 2px sur 100px = +2%
    }
    const sp = l.tree as any;
    expect(sp.sizes[0]).toBeCloseTo(56); // 50 + 3*2
    expect(sp.sizes[0] + sp.sizes[1]).toBeCloseTo(100);
    expect(validateLayout(l)).toEqual([]);
  });
});
