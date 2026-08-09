// graphe-agent.test.tsx — vérifie que le panel GrapheAgent se rend et charge
// le catalogue d'agents + affiche un agent en YAML ou graphe.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { GrapheAgentPanel } from '../panels/graphe-agent.panel.tsx';

// Polyfill ResizeObserver (React Flow l'utilise, absent en jsdom).
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
beforeEach(() => {
  (globalThis as any).ResizeObserver = ResizeObserverMock;
});

function mockApi(overrides: Record<string, any> = {}) {
  const post = vi.fn(async (route: string, params: any = {}) => {
    if (route === 'catalogue/agents/list') {
      return { result: { agents: [
        { name: 'codeur@v2', role: 'codeur', description: 'Agent de codage' },
        { name: 'relecteur@v2', role: 'relecteur', description: 'Reviewer' },
      ] } };
    }
    if (route === 'catalogue/agents/get') {
      if (params.name?.startsWith('codeur')) {
        const data = {
          role: 'codeur',
          entrypoints: { main: { steps: [
            { id: 'pick', type: 'call', fn: 'workspace/task_claim_next@v1', next: 'execute' },
            { id: 'execute', type: 'call', fn: 'workflow/autonomous_loop@v1', next: 'done', on_error: 'fail' },
            { id: 'done', type: 'call', fn: 'workspace/task_done@v1' },
            { id: 'fail', type: 'end', status: 'FAILED' },
          ] } },
        };
        return { result: { status: 'ok', yaml: 'name: codeur@v2\nrole: codeur', data } };
      }
      return { result: { status: 'error', error: 'introuvable' } };
    }
    return overrides[route] ?? { result: {} };
  });
  const ctx = { api: { post }, t: (k: string) => k };
  return { post, ctx };
}

describe('GrapheAgentPanel', () => {
  beforeEach(() => { vi.resetModules(); });

  it('liste le catalogue des agents', async () => {
    const { ctx } = mockApi();
    render(<GrapheAgentPanel ctx={ctx} />);
    await waitFor(() => expect(screen.getByText(/codeur@v2/)).toBeTruthy());
    expect(screen.getByText(/relecteur@v2/)).toBeTruthy();
  });

  it('affiche le graphe quand on sélectionne un agent', async () => {
    const { ctx } = mockApi();
    const { container } = render(<GrapheAgentPanel ctx={ctx} />);
    await waitFor(() => screen.getByText(/codeur@v2/));
    fireEvent.click(screen.getByText(/codeur@v2/));
    // Le graphe React Flow rend des nœuds dans le DOM (svg/foreignObject).
    await waitFor(() => expect(container.querySelector('.react-flow')).toBeTruthy());
    expect(container.querySelector('.react-flow__node')).toBeTruthy();
  });

  it('affiche le YAML en mode YAML', async () => {
    const { ctx } = mockApi();
    render(<GrapheAgentPanel ctx={ctx} />);
    await waitFor(() => screen.getByText(/codeur@v2/));
    fireEvent.click(screen.getByText(/codeur@v2/));
    // Bascule en mode YAML
    await waitFor(() => screen.getByText(/codeur@v2/));
    fireEvent.click(screen.getByText('panels.graphe-agent.yaml'));
    await waitFor(() => expect(screen.getByText(/name: codeur@v2/)).toBeTruthy());
  });
});
