import { describe, it, expect } from 'vitest';
import type { Layout } from '../layout/types.ts';
import { layoutToYaml, layoutFromYaml } from '../layout/persist.ts';
import { resolveLayout, listGroups } from '../layout/resolve.ts';

function layout(): Layout {
  return {
    id: 'main',
    label: 'Principale',
    tree: {
      type: 'split', direction: 'horizontal',
      children: [
        { type: 'group', id: 'pg-A', tabs: [{ panel: 'ressources', occId: 'o1' }], active: 'o1' },
        {
          type: 'group', id: 'pg-mini', active: 'm1',
          tabs: [{
            panel: '__mini__', occId: 'm1',
            tree: { type: 'group', id: 'pg-in', tabs: [{ panel: 'ide', occId: 'o2' }], active: 'o2' },
          }],
        },
      ],
    },
  };
}

describe('persist round-trip', () => {
  it('layout → YAML → layout conserve la structure', () => {
    const yaml = layoutToYaml(layout());
    const back = layoutFromYaml(yaml);
    expect(back.id).toBe('main');
    expect(back.tree.type).toBe('split');
    expect((back.tree as any).children.length).toBe(2);
    expect((back.tree as any).children[0].tabs[0].panel).toBe('ressources');
  });

  it('migre un ancien nœud miniLayout/slip vers un onglet __mini__', () => {
    const raw = `
id: main
tree:
  type: split
  direction: horizontal
  children:
    - type: group
      id: pg-A
      tabs: [{ panel: "chat", occId: "c1" }]
      active: "c1"
    - type: miniLayout
      id: mini-1
      title: IDE
      tree:
        type: group
        id: pg-in
        tabs: [{ panel: "ide", occId: "o2" }]
        active: "o2"
`;
    const back = layoutFromYaml(raw);
    const mini = (back.tree as any).children[1];
    expect(mini.type).toBe('group'); // migré en groupe
    const tab = mini.tabs.find((t: any) => t.tree);
    expect(tab).toBeTruthy();
    expect(tab.panel).toBe('__mini__');
    expect(tab.tree.tabs[0].panel).toBe('ide');
  });

  it('normalise un layout sans types (inférence)', () => {
    const raw = `
id: main
tree:
  direction: vertical
  children:
    - tabs: [{ panel: "chat", occId: "c1" }]
      active: "c1"
`;
    const back = layoutFromYaml(raw);
    const root = back.tree as any;
    expect(root.type).toBe('split');
    expect(root.children[0].type).toBe('group');
  });

  it('garantit occId sur chaque occurrence', () => {
    const raw = `
id: main
tree:
  type: group
  tabs:
    - panel: "chat"
`;
    const back = layoutFromYaml(raw);
    const g = back.tree as any;
    expect(g.tabs[0].occId).toBeTruthy();
  });
});

describe('resolveLayout', () => {
  it('résout la structure + les panels (y compris ceux des mini-layouts)', () => {
    const r = resolveLayout(layout());
    expect(r.root?.kind).toBe('split');
    expect(r.panelOccurrences.map((p) => p.panel)).toEqual(['ressources', '__mini__', 'ide']);
    expect(r.occIds).toEqual(['o1', 'm1', 'o2']);
  });

  it('construit un menu global avec les items fixes', () => {
    const r = resolveLayout(layout(), {});
    const labels = r.menu.map((m) => m.labelKey);
    expect(labels).toContain('menu.demarrer');
    expect(labels).toContain('menu.affichage');
    expect(labels).toContain('menu.configuration');
    expect(labels).toContain('menu.aide');
  });

  it('l\'item menu d\'un panel ciblant Affichage est inséré directement (pas de sous-section)', () => {
    // Layout avec un panel 'graphe-agent' ouvert.
    const l: Layout = {
      ...layout(),
      tree: {
        type: 'split', direction: 'horizontal',
        children: [
          { type: 'group', id: 'pg-A', tabs: [{ panel: 'graphe-agent', occId: 'o1' }], active: 'o1' },
        ],
      },
    };
    const r = resolveLayout(l, {
      'graphe-agent': [
        { labelKey: 'menu.themeGraphe', action: 'theme-graphe:set', path: ['menu.affichage'] },
      ],
    });
    const affichage = r.menu.find((m) => m.labelKey === 'menu.affichage')!;
    expect(affichage).toBeTruthy();
    const labels = affichage.items!.map((i) => i.labelKey ?? (i.style === 'section-header' ? 'header' : 'sep'));
    // L'item theme-graphe est DANS affichage directement, pas dans un sous-menu "Affichage".
    expect(labels).toContain('menu.themeGraphe');
    // Pas de sous-menu imbriqué nommé "Affichage".
    expect(affichage.items!.some((i) => i.labelKey === 'menu.affichage' && i.items)).toBe(false);
    // L'item garde son action (transformée en radio par App).
    const item = affichage.items!.find((i) => i.action === 'theme-graphe:set');
    expect(item).toBeTruthy();
  });

  it('menu Affichage : Panneaux ouverts + Ouvrir nouveau (bundles) + Mini-layout + Refresh', () => {    const catalogue = [
      { id: 'monitoring-processus', labelKey: 'panels.monitoring-processus.titre', bundles: ['monitoring'] },
      { id: 'chat', labelKey: 'panels.chat.titre', bundles: ['communication'] },
      { id: 'sans-bundle', labelKey: 'panels.sans-bundle.titre' }, // → Autres
    ];
    const r = resolveLayout(layout(), {}, null, catalogue);
    const affichage = r.menu.find((m) => m.labelKey === 'menu.affichage')!;
    expect(affichage).toBeTruthy();
    const labels = affichage.items!.map((i) => i.labelKey ?? 'sep');
    // Thèmes, sép, Panneaux ouverts, sép, Ouvrir nouveau, sép, Mini-layout, sép, Plein écran, Refresh
    expect(labels).toEqual([
      'menu.themes', 'sep', 'menu.panneauxOuverts', 'sep', 'menu.ouvrirNouveau', 'sep', 'menu.miniLayout', 'sep', 'menu.pleinEcran', 'menu.refresh',
    ]);
    // "Panneaux ouverts" liste les occurrences actives (ressources, __mini__, ide)
    const ouverts = affichage.items![2];
    expect(ouverts.labelKey).toBe('menu.panneauxOuverts');
    expect(ouverts.items!.length).toBe(3);
    // "Ouvrir un nouveau panneau" : un sous-menu par bundle (communication, monitoring, Autres)
    const nouveau = affichage.items![4];
    const bundles = nouveau.items!.map((b) => b.labelKey);
    expect(bundles).toEqual(['bundle.communication', 'bundle.monitoring', 'bundle.autres']);
    const monitoring = nouveau.items![1];
    expect(monitoring.items!.length).toBe(1);
    expect(monitoring.items![0].labelKey).toBe('panels.monitoring-processus.titre');
    expect(monitoring.items![0].suffix).toBe('+'); // pas présent dans le layout
  });

  it('menus globaux : Démarrer, Affichage, Configuration, Aide', () => {
    const r = resolveLayout(layout(), {}, null, []);
    const labels = r.menu.map((m) => m.labelKey);
    expect(labels).toContain('menu.demarrer');
    expect(labels).toContain('menu.affichage');
    expect(labels).toContain('menu.configuration');
    expect(labels).toContain('menu.aide');
    // plus de menu Fenêtre/Langue racines (fusionnés)
    expect(labels).not.toContain('menu.fenetre');
    expect(labels).not.toContain('menu.langue');
  });

  it('sessions : Ouvrir désactivé si active, Fermer désactivé si inactive', () => {
    const wm = {
      official: [], registered: [], live: [],
      sessions: [
        { id: 's1', name: 'Active', open_windows: [] },
        { id: 's2', name: 'Inactive', open_windows: [] },
      ],
      activeSession: { id: 's1', name: 'Active' },
      liveLabels: [],
    };
    const r = resolveLayout(layout(), {}, null, [], wm);
    const affichage = r.menu.find((m) => m.labelKey === 'menu.affichage')!;
    const sessions = affichage.items!.find((i) => i.labelKey === 'menu.sessions')!;
    const [s1, s2] = sessions.items!.filter((i) => i.action !== 'session:new');
    // s1 (active) : Ouvrir désactivé, Fermer actif
    expect(s1.items!.find((i) => i.action === 'session:open:s1')?.disabled).toBe(true);
    expect(s1.items!.find((i) => i.action === 'session:close:s1')?.disabled).toBeFalsy();
    // s2 (inactive) : Ouvrir actif, Fermer désactivé
    expect(s2.items!.find((i) => i.action === 'session:open:s2')?.disabled).toBeFalsy();
    expect(s2.items!.find((i) => i.action === 'session:close:s2')?.disabled).toBe(true);
  });
});

describe('listGroups', () => {
  it('liste tous les groupes visibles (y compris ceux des mini-layouts)', () => {
    const groups = listGroups(layout().tree);
    expect(groups.length).toBe(3); // pg-A + pg-mini (conteneur) + pg-in (interne)
  });
});
