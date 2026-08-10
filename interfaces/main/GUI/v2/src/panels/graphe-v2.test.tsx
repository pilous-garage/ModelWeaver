// graphe-v2.test.tsx — le panel V2 affiche le graphe FSM du BACKEND.
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { GrapheV2Panel } from './graphe-v2.panel.tsx';

class ResizeObserverMock { observe() {} unobserve() {} disconnect() {} }
beforeEach(() => { (globalThis as any).ResizeObserver = ResizeObserverMock; });

describe('GrapheV2Panel', () => {
  it('liste les agents puis affiche le graphe du backend (graphe_utile/yaml_to_fsm)', async () => {
    const post = vi.fn(async (route: string) => {
      if (route === 'catalogue/agents/list') {
        return { result: { agents: [{ name: 'greedy-coder', role: 'codeur', description: 'x' }] } };
      }
      if (route === 'graphe_utile/yaml_to_fsm') {
        return { result: { status: 'ok', title: 'backend', nodes: [
          { id: 'main', type: 'entrypoint', label: 'main', ref: 'entry', tags: ['entrypoint'], vars: {} },
          { id: 'BACKEND_ONLY', type: 'skill', label: 'backend', ref: 'git/end_exec@v1', tags: ['tok_out'], vars: {} },
          { id: 'end', type: 'exitpoint', label: 'end', ref: 'end', tags: [], vars: {} },
        ], edges: [
          { from: 'main', to: 'BACKEND_ONLY', label: 'entry', type: 'next' },
          { from: 'BACKEND_ONLY', to: 'end', label: 'next', type: 'next' },
        ] } };
      }
      return { result: {} };
    });
    const ctx = { api: { post }, t: (k: string) => k };
    const { container } = render(<GrapheV2Panel ctx={ctx} />);
    await waitFor(() => screen.getByText(/greedy-coder/));
    fireEvent.click(screen.getByText(/greedy-coder/));
    // Le graphe vient du backend (nœud BACKEND_ONLY).
    await waitFor(() => expect(container.querySelector('[data-id="BACKEND_ONLY"]')).toBeTruthy());
    expect(post).toHaveBeenCalledWith('graphe_utile/yaml_to_fsm', { name: 'greedy-coder' });
  });
});
