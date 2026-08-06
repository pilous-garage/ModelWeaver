import { describe, it, expect } from 'vitest';
import type { GroupNode, Layout } from '../layout/types.ts';
import {
  addPanel, closeTab, activateTab, moveTab, splitGroup, reorderTabs, renameTab,
  addMiniLayout, closeMiniLayout, mapMiniLayout, findGroupById, validateLayout, findGroup, findPanelOccurrence, listPresentPanels, paramsEqual,
  resizeSplit,
  setZoom, setZoomLocal, toggleZoomLock, setGlobalZoom, resetZoom, effectiveZoom,
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

  it('la recherche traverse les mini-layouts (sous-layout dans un onglet)', () => {
    // mini-layout = onglet avec tree interne contenant un panel
    let l = addMiniLayout(baseLayout(), 'pg-A', 'chat');
    const mini = findGroup(l.tree, 'occ-1');
    expect(mini).not.toBeNull();
    const tab = mini!.tabs.find((t) => t.tree);
    expect(tab).toBeTruthy();
    // ajoute un panel DANS le tree interne du mini-layout
    const inner = tab!.tree as GroupNode;
    inner.tabs = [...inner.tabs, { panel: 'chat', occId: 'occ-mini-chat' }];
    inner.active = 'occ-mini-chat';
    // findPanelOccurrence doit traverser l'onglet mini-layout
    expect(findPanelOccurrence(l.tree, 'chat')).not.toBeNull();
    expect(validateLayout(l)).toEqual([]);
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
  it('ajoute un onglet mini-layout (avec tree interne vide) dans le groupe', () => {
    const l = addMiniLayout(baseLayout(), 'pg-A', 'ide');
    expect(validateLayout(l)).toEqual([]);
    const group = findGroupById(l.tree, 'pg-A')!;
    const mini = group.tabs.find((t) => t.tree);
    expect(mini).toBeTruthy();
    expect(mini!.panel).toBe('__mini__');
    expect((mini!.tree as any).type).toBe('group');
    expect((mini!.tree as any).tabs).toHaveLength(0); // conteneur vide
  });

  it('permet PLUSIEURS mini-layouts dans un même groupe (onglets)', () => {
    let l = addMiniLayout(baseLayout(), 'pg-A', 'ide');
    l = addMiniLayout(l, 'pg-A', 'ide2');
    const group = findGroupById(l.tree, 'pg-A')!;
    const minis = group.tabs.filter((t) => t.tree);
    expect(minis).toHaveLength(2);
    expect(validateLayout(l)).toEqual([]);
  });

  it('mapMiniLayout applique une op au tree INTERNE de l\'onglet visé', () => {
    const l0 = addMiniLayout(baseLayout(), 'pg-A', 'ide');
    const mini = findGroupById(l0.tree, 'pg-A')!.tabs.find((t) => t.tree)!;
    // ajoute un panel dans le mini-layout via mapMiniLayout
    const l1 = mapMiniLayout(l0, mini.occId, (tree) => {
      const g = tree as any;
      g.tabs = [...g.tabs, { panel: 'chat', occId: 'occ-m1' }];
      g.active = 'occ-m1';
      return g;
    });
    const mini2 = findGroupById(l1.tree, 'pg-A')!.tabs.find((t) => t.tree)!;
    expect((mini2.tree as any).tabs).toHaveLength(1);
    expect(findPanelOccurrence(l1.tree, 'chat')).not.toBeNull();
    expect(validateLayout(l1)).toEqual([]);
    // les autres onglets du groupe restent intacts (1 onglet normal + 1 mini)
    const group = findGroupById(l1.tree, 'pg-A')!;
    expect(group.tabs.filter((t) => !t.tree)).toHaveLength(1);
    expect(group.tabs.filter((t) => t.tree)).toHaveLength(1);
  });

  it('retirer le dernier panel d\'un mini-layout le laisse VIDE (conteneur)', () => {
    let l = addMiniLayout(baseLayout(), 'pg-A', 'ide');
    const mini = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.tree)!;
    l = mapMiniLayout(l, mini.occId, (tree) => {
      const g = tree as any;
      g.tabs = [...g.tabs, { panel: 'chat', occId: 'occ-m1' }];
      g.active = 'occ-m1';
      return g;
    });
    // retire l'occ-m1 du mini-layout → le groupe interne doit rester (vide)
    l = closeTab(l, (findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.tree)!.tree as any).id, 'occ-m1');
    const mini2 = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.tree)!;
    expect(mini2).toBeTruthy(); // le conteneur reste
    expect((mini2.tree as any).tabs).toHaveLength(0);
    expect(validateLayout(l)).toEqual([]);
  });

  it('closeMiniLayout retire l\'onglet mini-layout du groupe', () => {
    const l0 = addMiniLayout(baseLayout(), 'pg-A', 'ide');
    const mini = findGroupById(l0.tree, 'pg-A')!.tabs.find((t) => t.tree)!;
    const l1 = closeMiniLayout(l0, mini.occId);
    const group = findGroupById(l1.tree, 'pg-A')!;
    expect(group.tabs.filter((t) => t.tree)).toHaveLength(0);
    expect(validateLayout(l1)).toEqual([]);
  });
});

describe('resizeSplit', () => {
  it('positionne le séparateur via la zone utilisable (total − séparateur)', () => {
    // 2 enfants + 1 séparateur de 4px → usable = 1000−4 = 996
    // séparateur (centre) à 200px → panneau index = (200−2)/996*100 ≈ 19.88
    const l = resizeSplit(baseLayout(), 'sp-test', 0, 200, 1000);
    expect(l.tree.type).toBe('split');
    const sp = l.tree as any;
    expect(sp.sizes[0]).toBeCloseTo((200 - 2) / 996 * 100, 6);
    expect(sp.sizes[0] + sp.sizes[1]).toBeCloseTo(100);
    expect(validateLayout(l)).toEqual([]);
  });

  it('le séparateur suit EXACTEMENT la position (conversion px↔% exacte)', () => {
    // On vérifie l'aller-retour : pct → px rendu (via usable) = position demandée
    let l = baseLayout();
    for (const pos of [300, 500, 250, 700]) {
      l = resizeSplit(l, 'sp-test', 0, pos, 1000);
      const sp = l.tree as any;
      // px rendu = 2 + sp.sizes[0]/100 * 996 (panneau gauche + moitié séparateur)
      const rendered = 2 + sp.sizes[0] / 100 * 996;
      expect(rendered).toBeCloseTo(pos, 6);
      expect(sp.sizes[0] + sp.sizes[1]).toBeCloseTo(100);
    }
    expect(validateLayout(l)).toEqual([]);
  });

  it('borne le resize pour ne pas écraser un enfant', () => {
    const l = resizeSplit(baseLayout(), 'sp-test', 0, 2000, 1000);
    const sp = l.tree as any;
    expect(sp.sizes[0]).toBe(95); // plafond 95%
    expect(sp.sizes[1]).toBe(5);
  });
});

describe('renameTab', () => {
  it('set un titre personnalisé sur un onglet', () => {
    const l = renameTab(baseLayout(), 'pg-A', 'occ-1', 'Mes ressources');
    const occ = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(occ.label).toBe('Mes ressources');
    // l'identifiant du panel et l'occId ne changent PAS (renommage = label seul)
    expect(occ.panel).toBe('ressources');
    expect(occ.occId).toBe('occ-1');
    expect(validateLayout(l)).toEqual([]);
  });

  it('retire le titre si le label est vide/null', () => {
    let l = renameTab(baseLayout(), 'pg-A', 'occ-1', 'Titre');
    l = renameTab(l, 'pg-A', 'occ-1', '   ');
    const occ = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(occ.label).toBeUndefined();
  });

  it('ne touche pas aux autres onglets', () => {
    let l = addPanel(baseLayout(), 'pg-A', 'chat'); // occ-2 ajouté
    l = renameTab(l, 'pg-A', 'occ-1', 'Renommé');
    const occ2 = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId !== 'occ-1')!;
    expect(occ2.label).toBeUndefined();
  });
});

describe('zoom', () => {
  it('setZoom fixe le zoom propre = effective / ancestorFactor', () => {
    // ancêtre global ×1 → setZoom effectif 2.0 sur occ-1 → propre = 2.0
    let l = baseLayout();
    l = setZoom(l, 'occ-1', 2.0, 1);
    const occ = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(occ.zoom?.value).toBe(2.0);
  });

  it('setZoom avec un facteur d\'ancêtres (mini-layout) → propre = effective / factor', () => {
    // ancêtres ×2 → pour un effectif de 3.0, le propre doit être 1.5
    const l = setZoom(baseLayout(), 'occ-1', 3.0, 2);
    const occ = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(occ.zoom?.value).toBeCloseTo(1.5, 6);
  });

  it('setZoomLocal fixe la valeur LOCALE directement (sans ancêtres)', () => {
    let l = baseLayout();
    l = setZoomLocal(l, 'occ-1', 1.1);
    const occ = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(occ.zoom?.value).toBeCloseTo(1.1, 6);
    // l'effectif = ancêtres (1.1) × locale (1.1) = 1.21 ; la valeur locale reste 1.1
    expect(effectiveZoom(1.1, occ)).toBeCloseTo(1.21, 6);
    expect(occ.zoom?.value).toBeCloseTo(1.1, 6);
  });

  it('setZoomLocal est ignoré sur un onglet locké', () => {
    let l = baseLayout();
    l = setZoomLocal(l, 'occ-1', 1.5);
    l = toggleZoomLock(l, 'occ-1', 1);
    const before = JSON.stringify(l);
    l = setZoomLocal(l, 'occ-1', 2.0);
    expect(JSON.stringify(l)).toBe(before);
  });

  it('toggleZoomLock locke en ABSORBANT le total (valeur figée)', () => {
    // propre 2.0, ancêtre ×1 → effectif 2.0 → lock absorbe 2.0
    let l = setZoom(baseLayout(), 'occ-1', 2.0, 1);
    l = toggleZoomLock(l, 'occ-1', 1);
    const occ = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(occ.zoom?.locked).toBe(true);
    expect(occ.zoom?.value).toBe(2.0);
    // l'affichage (effectif) ne change pas au lock : 2.0
    expect(effectiveZoom(1, occ)).toBe(2.0);
  });

  it('lock absorbe le total quand ancêtre > 1 ; délock recalcule ÷ parents', () => {
    // ancêtre ×2, propre 1.5 → effectif 3.0 → lock absorbe 3.0
    let l = setZoom(baseLayout(), 'occ-1', 3.0, 2); // propre 1.5
    l = toggleZoomLock(l, 'occ-1', 2);
    const locked = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(locked.zoom?.value).toBeCloseTo(3.0, 6); // total absorbé
    // effectif affiché = valeur directe (pas re-multipliée par l'ancêtre)
    expect(effectiveZoom(2, locked)).toBeCloseTo(3.0, 6);
    // les ancêtres changent (global 2 → 1) : le locké ne bouge PAS (figé)
    l = setGlobalZoom(l, 1);
    const stillLocked = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(stillLocked.zoom?.value).toBeCloseTo(3.0, 6);
    expect(effectiveZoom(1, stillLocked)).toBeCloseTo(3.0, 6);
    // délock : recalcule propre = valeur lockée ÷ ancêtres ACTUELS (×1) = 3.0
    l = toggleZoomLock(l, 'occ-1', 1);
    const unlocked = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(unlocked.zoom?.locked).toBe(false);
    expect(unlocked.zoom?.value).toBeCloseTo(3.0, 6);
  });

  it('exemple utilisateur : 110% → 110% → 110%, lock → 133%, dézoom global, délock → 121%', () => {
    // global 110%, mini 110%, panel 110% : ancêtres = 1.1 × 1.1 = 1.21,
    // on vise un effectif de 1.331 (133%) → propre = 1.331 / 1.21 = 1.1
    let l = baseLayout();
    l = setZoom(l, 'occ-1', 1.331, 1.21); // propre = 1.1
    l = toggleZoomLock(l, 'occ-1', 1.21); // lock : 1.21 × 1.1 = 1.331 (133%)
    const locked = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(locked.zoom?.value).toBeCloseTo(1.331, 5);
    // dézoom global à 100% → ancêtres actuels = 1.0 × 1.1 = 1.1 ; le locké ne bouge pas
    expect(effectiveZoom(1.1, locked)).toBeCloseTo(1.331, 5);
    // délock avec ancêtres actuels 1.1 : 1.331 / 1.1 = 1.21 (121%)
    l = toggleZoomLock(l, 'occ-1', 1.1);
    const unlocked = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(unlocked.zoom?.value).toBeCloseTo(1.21, 5);
  });

  it('un onglet locké ignore setZoom', () => {
    let l = setZoom(baseLayout(), 'occ-1', 2.0, 1);
    l = toggleZoomLock(l, 'occ-1', 1);
    const before = JSON.stringify(l);
    l = setZoom(l, 'occ-1', 5.0, 1);
    expect(JSON.stringify(l)).toBe(before);
  });

  it('un changement de global N\'affecte PAS un panel locké (valeur figée)', () => {
    let l = setGlobalZoom(baseLayout(), 1);
    l = setZoom(l, 'occ-1', 2.0, 1);
    l = toggleZoomLock(l, 'occ-1', 1); // lock : value 2.0
    l = setGlobalZoom(l, 2.0);
    const occ = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    // la VALEUR lockée ne change pas (elle est non-multipliée, figée)
    expect(occ.zoom?.value).toBeCloseTo(2.0, 6);
    expect(occ.zoom?.locked).toBe(true);
    // l'affichage (effectif) = valeur directe, indépendante du global
    expect(effectiveZoom(2.0, occ)).toBeCloseTo(2.0, 6);
  });

  it('un changement du zoom d\'un mini-layout N\'affecte PAS un panel interne locké', () => {
    // construit un mini-layout (onglet __mini__) avec un panel locké dedans
    let l = addMiniLayout(baseLayout(), 'pg-A', 'ide');
    const mini = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.tree)!;
    l = mapMiniLayout(l, mini.occId, (tree) => {
      const g = tree as any;
      g.tabs = [{ panel: 'chat', occId: 'occ-in', zoom: { value: 1 } }];
      g.active = 'occ-in';
      return g;
    });
    // locke le panel interne (ancêtre = zoom du mini = 1) → value 1.0 figé
    l = toggleZoomLock(l, 'occ-in', 1);
    // le mini passe à 2× → le panel locké ne DOIT PAS bouger
    l = setZoom(l, mini.occId, 2.0, 1);
    const inner = (() => {
      const m = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.tree)!;
      return (m.tree as any).tabs.find((t: any) => t.occId === 'occ-in');
    })();
    expect(inner.zoom.value).toBeCloseTo(1.0, 6);
    expect(inner.zoom.locked).toBe(true);
    // affichage = valeur directe figée (indépendante du mini)
    expect(effectiveZoom(1 * 2, inner)).toBeCloseTo(1.0, 6);
  });

  it('resetZoom remet le propre à 1 et délocke', () => {
    let l = setZoom(baseLayout(), 'occ-1', 3.0, 1);
    l = toggleZoomLock(l, 'occ-1', 1);
    l = resetZoom(l, 'occ-1');
    const occ = findGroupById(l.tree, 'pg-A')!.tabs.find((t) => t.occId === 'occ-1')!;
    expect(occ.zoom).toBeUndefined();
  });
});
