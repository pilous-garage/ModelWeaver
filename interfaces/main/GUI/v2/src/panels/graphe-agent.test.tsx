// graphe-agent.test.tsx — vérifie que le panel GrapheAgent se rend et charge
// le catalogue d'agents + affiche un agent en YAML ou graphe.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { GrapheAgentPanel, agentYamlToTaskflow } from '../panels/graphe-agent.panel.tsx';

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

describe('agentYamlToTaskflow', () => {
  it('déduit les steps + tokens (pick coding → end_exec → code_review)', () => {
    const data = {
      role: 'codeur',
      entrypoints: { main: { steps: [
        { id: 'pick', type: 'call', fn: 'workspace/token_task_pick@v1',
          inputs: { task_types: '[{type: coding, max_difficulty: expert}]' } },
        { id: 'clone', type: 'call', fn: 'git/git_clone@v1' },
        { id: 'exec_loop', type: 'while', body: { steps: [
          { id: 'do_work', type: 'llm_call' },
          { id: 'post', type: 'call', fn: 'git/end_exec@v1' },
        ] } },
        { id: 'end', type: 'end' },
      ] } },
    };
    const g = agentYamlToTaskflow(data, 'greedy-coder');
    const types = g.nodes.map((n: any) => n.type);
    const ids = g.nodes.map((n: any) => n.id);
    // Steps présents (y compris le corps de boucle)
    expect(types).toContain('call');
    expect(types).toContain('llm');
    // Token-in : coding (type pioché, pas le placeholder)
    expect(types).toContain('token-in');
    expect(ids.some((x: string) => x === 'coding')).toBe(true);
    // Token-out : code_review (transition produite par end_exec)
    expect(types).toContain('token-out');
    expect(ids.some((x: string) => x.includes('code_review'))).toBe(true);
  });

  it('produit les tokens via les tools des steps llm_call (pas par end)', () => {
    const data = { role: 'codeur', entrypoints: { main: { steps: [
      { id: 'pick', type: 'call', fn: 'workspace/token_task_pick@v1',
        inputs: { task_types: '[{type: coding, max_difficulty: expert}]' } },
      { id: 'do_work', type: 'llm_call', bundles: ['dev'] },
      { id: 'ask_verdict', type: 'llm_call', bundles: ['workspace_verdict'] },
      { id: 'end', type: 'end', status: 'SUCCESS' },
      { id: 'fail', type: 'end', status: 'FAILED' },
    ] } } };
    const g = agentYamlToTaskflow(data, 'greedy-coder');
    const ids = g.nodes.map((n: any) => n.id);
    // token-in : coding (pioché)
    expect(ids).toContain('coding');
    // do_work (bundles dev → end_exec) produit code_review (transition)
    expect(ids).toContain('code_review');
    const crEdge = g.edges.find((e: any) => e.to === 'code_review');
    expect(crEdge.from).toBe('main_do_work');
    // ask_verdict (workspace_verdict → task_verdict) produit done
    expect(ids).toContain('done');
    const doneEdges = g.edges.filter((e: any) => e.to === 'done');
    expect(doneEdges.length).toBeGreaterThan(0);
    // done vient d'un step llm_call (tool token), jamais du step end
    for (const e of doneEdges) {
      expect(e.from).not.toBe('main_end');
      expect(e.from).not.toBe('main_fail');
    }
    // PAS de done relié au step end (terminaison sans skill token)
    expect(g.edges.some((e: any) => e.from === 'main_end' && e.to === 'done')).toBe(false);
  });

  it('produit erreur_agent/too_hard via coding_work + end_exec via finalisation', () => {
    const data = { role: 'codeur', entrypoints: { main: { steps: [
      { id: 'pick', type: 'call', fn: 'workspace/token_task_pick@v1',
        inputs: { task_types: '[{type: coding, max_difficulty: expert}]' } },
      { id: 'do_work', type: 'llm_call', bundles: ['coding_work'] },
      { id: 'finalisation', type: 'call', fn: 'git/end_exec@v1' },
      { id: 'release', type: 'call', fn: 'workspace/token_task_release@v1' },
    ] } } };
    const g = agentYamlToTaskflow(data, 'greedy-coder');
    const ids = g.nodes.map((n: any) => n.id);
    // do_work (coding_work → exit_loop_too_hard) produit too_hard
    expect(ids).toContain('too_hard');
    // finalisation (end_exec) produit code_review (transition)
    expect(ids).toContain('code_review');
    const crEdge = g.edges.find((e: any) => e.to === 'code_review');
    expect(crEdge.from).toBe('main_finalisation');
    // release produit erreur_agent
    expect(ids).toContain('erreur_agent');
    const eaEdge = g.edges.find((e: any) => e.to === 'erreur_agent');
    expect(eaEdge.from).toBe('main_release');
  });

  it('résout le task_type par défaut quand le rôle est inconnu', () => {
    const data = { role: 'explore', entrypoints: { main: { steps: [
      { id: 'pick', type: 'call', fn: 'workspace/token_task_pick@v1',
        inputs: { task_types: '[{type: exploration, max_difficulty: expert}]' } },
    ] } } };
    const g = agentYamlToTaskflow(data, 'explore');
    expect(g.nodes.map((n: any) => n.id)).toContain('exploration');
  });
});
