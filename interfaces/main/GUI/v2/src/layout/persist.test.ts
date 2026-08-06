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
        { type: "miniLayout", id: 'mini-1', title: 'IDE', tree: { type: 'group', id: 'pg-in', tabs: [{ panel: 'ide', occId: 'o2' }], active: 'o2' } },
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
  it('résout la structure + liste les MiniLayouts + les panels', () => {
    const r = resolveLayout(layout());
    expect(r.root?.kind).toBe('split');
    expect(r.MiniLayouts.length).toBe(1);
    expect(r.MiniLayouts[0].id).toBe('mini-1');
    expect(r.panelOccurrences.map((p) => p.panel)).toEqual(['ressources', 'ide']);
    expect(r.occIds).toEqual(['o1', 'o2']);
  });

  it('construit un menu global avec les items fixes', () => {
    const r = resolveLayout(layout(), {});
    const labels = r.menu.map((m) => m.labelKey);
    expect(labels).toContain('menu.fichier');
    expect(labels).toContain('menu.fenetre');
    expect(labels).toContain('menu.langue');
  });

  it('n\'injecte le menu d\'une slip que si elle est active', () => {
    const slipMenu = [{ labelKey: 'panels.ide.menu.x', action: 'mini-layout:menu:x' }];
    // ajoute menuExtra à la slip
    const l = layout();
    (l.tree as any).children[1].menuExtra = slipMenu;

    const inactive = resolveLayout(l, {}, null);
    expect(inactive.menu.some((m) => m.action === 'mini-layout:menu:x')).toBe(false);

    const active = resolveLayout(l, {}, 'mini-1');
    expect(active.menu.some((m) => m.action === 'mini-layout:menu:x')).toBe(true);
  });
});

describe('listGroups', () => {
  it('liste tous les groupes visibles (y compris ceux des MiniLayouts)', () => {
    const groups = listGroups(layout().tree);
    expect(groups.length).toBe(2); // pg-A + pg-in
  });
});
