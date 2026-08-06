// Test d'intégration du rendu : monte App avec un layout, vérifie que les
// onglets, splits et panels s'affichent, et que les actions (ouvrir/fermer)
// modifient le DOM via les opérations.

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { render, fireEvent, cleanup } from '@testing-library/react';
import { App } from './App.tsx';
import { layoutFromYaml } from './layout/persist.ts';
import { initPanels } from './panels/init.ts';

const LAYOUT = `
id: test
tree:
  type: split
  direction: horizontal
  children:
    - type: group
      id: pg-gauche
      tabs:
        - { panel: ressources, occId: occ-1 }
        - { panel: etat-systeme, occId: occ-2 }
      active: occ-1
    - type: group
      id: pg-droit
      tabs:
        - { panel: etat-simple, occId: occ-3 }
      active: occ-3
`;

describe('rendu de App', () => {
  let layouts: any[] = [];

  beforeEach(async () => {
    cleanup();
    layouts = [];
    await initPanels();
  });

  it('affiche les onglets de chaque groupe', () => {
    const layout = layoutFromYaml(LAYOUT);
    const { container } = render(<App layout={layout} windowId="w1" onChange={(l) => layouts.push(l)} />);
    // onglets du groupe gauche
    const tabs = container.querySelectorAll('[data-testid^="tab-"]');
    const labels = Array.from(tabs).map((t) => t.getAttribute('data-testid'));
    expect(labels).toContain('tab-ressources');
    expect(labels).toContain('tab-etat-systeme');
    expect(labels).toContain('tab-etat-simple');
  });

  it('affiche le contenu du panel actif', () => {
    const layout = layoutFromYaml(LAYOUT);
    const { container } = render(<App layout={layout} windowId="w1" />);
    // le panel ressources (actif) est visible
    expect(container.querySelector('.mw-panel-ressources')).toBeTruthy();
  });

  it('ferme un onglet et met à jour le DOM', () => {
    const layout = layoutFromYaml(LAYOUT);
    const { container } = render(<App layout={layout} windowId="w1" onChange={(l) => layouts.push(l)} />);
    const closeRessources = container.querySelector('[data-testid="tab-close-ressources"]');
    expect(closeRessources).toBeTruthy();
    fireEvent.click(closeRessources!);
    // l'onglet ressources a disparu
    const tabs = container.querySelectorAll('[data-testid^="tab-"]');
    const labels = Array.from(tabs).map((t) => t.getAttribute('data-testid'));
    expect(labels).not.toContain('tab-ressources');
  });

  it('active un autre onglet au clic', () => {
    const layout = layoutFromYaml(LAYOUT);
    const { container } = render(<App layout={layout} windowId="w1" />);
    const tabEtat = container.querySelector('[data-testid="tab-etat-systeme"]')!;
    fireEvent.click(tabEtat!);
    // le panel etat-systeme est maintenant actif
    expect(container.querySelector('.mw-panel-etat')).toBeTruthy();
  });

  it('un clic simple (mousedown+mouseup+click) active sans split', () => {
    const layout = layoutFromYaml(LAYOUT);
    const { container } = render(<App layout={layout} windowId="w1" />);
    const tabEtat = container.querySelector('[data-testid="tab-etat-systeme"]')!;
    // flux exact du simulateur gui/act : mousedown puis mouseup puis click
    // (aucun mousemove → pas de drag → le click active l'onglet)
    fireEvent.mouseDown(tabEtat, { button: 0 });
    fireEvent.mouseUp(tabEtat, { button: 0 });
    fireEvent.click(tabEtat);
    // le panel etat-systeme est actif, AUCUN split créé (les 3 onglets restent)
    expect(container.querySelector('.mw-panel-etat')).toBeTruthy();
    const tabs = container.querySelectorAll('[data-testid^="tab-"]');
    const labels = Array.from(tabs).map((t) => t.getAttribute('data-testid'));
    expect(labels).toContain('tab-etat-systeme');
    expect(labels).toContain('tab-ressources');
    expect(labels).toContain('tab-etat-simple');
  });
});
