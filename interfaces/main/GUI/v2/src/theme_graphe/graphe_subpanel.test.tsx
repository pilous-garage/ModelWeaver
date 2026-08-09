// graphe_subpanel.test.tsx — vérifie le rendu du module graphe générique.
// Monte GrapheSubPanel avec un graph.yaml de test et vérifie nœuds + arêtes.

import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GrapheSubPanel } from './graphe_subpanel.tsx';

const TEST_GRAPH = `
id: "graphe-test"
title: "Test"
nodes:
  - { id: "pick",  type: "entry", label: "Piocher",  ref: "task_claim_next", tags: ["entrypoint"] }
  - { id: "exec",  type: "llm",   label: "Exécuter", ref: "autonomous_loop", tags: ["heavy"] }
  - { id: "done",  type: "exit",  label: "Terminer", ref: "task_done", tags: ["exit"] }
edges:
  - { from: "pick", to: "exec", label: "next", type: "next" }
  - { from: "exec", to: "done", label: "success", type: "success" }
  - { from: "pick", to: "done", label: "err", type: "error" }
`;

describe('GrapheSubPanel', () => {
  it('rend les nœuds du graph.yaml', () => {
    render(<GrapheSubPanel doc={TEST_GRAPH} engine="svg" />);
    // Le moteur SVG rend le titre + les labels des nœuds.
    expect(screen.getByText(/Test/)).toBeTruthy();
    expect(screen.getByText(/Piocher/)).toBeTruthy();
    expect(screen.getByText(/Exécuter/)).toBeTruthy();
    expect(screen.getByText(/Terminer/)).toBeTruthy();
  });

  it('parse un objet GraphDoc directement', () => {
    const doc = {
      id: 'obj', title: 'Objet',
      nodes: [{ id: 'a', type: 'call', label: 'A' }],
      edges: [],
    };
    render(<GrapheSubPanel doc={doc} engine="svg" />);
    expect(screen.getByText(/A/)).toBeTruthy();
  });

  it('affiche "Aucun graphe" si doc null', () => {
    render(<GrapheSubPanel doc={null} engine="svg" />);
    expect(screen.getByText(/Aucun graphe/)).toBeTruthy();
  });

  it('exporte le SVG via onExportSvg', () => {
    const onExport = vi.fn();
    render(<GrapheSubPanel doc={TEST_GRAPH} engine="svg" onExportSvg={onExport} />);
    const btn = screen.getByText(/SVG/);
    btn.click();
    // Le SVG doit contenir des <svg> avec les nœuds.
    const svg = onExport.mock.calls[0]?.[0] as string;
    expect(svg).toContain('<svg');
    expect(svg).toContain('Piocher');
  });
});
