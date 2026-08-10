// graphe-agent.test.tsx — vérifie que le panel GrapheAgent se rend et charge
// le catalogue d'agents + affiche un agent en YAML ou graphe.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import fs from 'fs';
import path from 'path';
import { parse as yamlParse } from 'yaml';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { GrapheAgentPanel, agentYamlToTaskflow, agentYamlToGraph, validateFsmGraph, check_fsm, prefixGraphIds } from '../panels/graphe-agent.panel.tsx';
import { buildTaskflowDoc } from '../panels/taskflowBuild.ts';
import { zipAll, unzipAll } from '../panels/taskflowClip.ts';

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

  it('"Tout déplier" révèle les sous-nœuds (body de boucle) dans le DOM', async () => {
    // Mock avec une boucle while (body dépliable) pour tester les containers.
    const post = vi.fn(async (route: string, params: any = {}) => {
      if (route === 'catalogue/agents/list') {
        return { result: { agents: [
          { name: 'codeur@v2', role: 'codeur', description: 'Agent de codage' },
        ] } };
      }
      if (route === 'catalogue/agents/get') {
        const data = { role: 'codeur', entrypoints: { main: { steps: [
          { id: 'pick', type: 'call', fn: 'workspace/task_claim_next@v1', next: 'work' },
          { id: 'work', type: 'while', next: 'after', condition: 'x<3',
            body: { steps: [
              { id: 'do', type: 'llm_call', next: 'chk' },
              { id: 'chk', type: 'switch', variable: 'st',
                conditions: [{ operator: 'EQUALS', value: 'done', next: 'fin' }],
                default: 'do' },
              { id: 'fin', type: 'set_variable', next: 'bd' },
              { id: 'bd', type: 'break' },
            ] } },
          { id: 'after', type: 'call', fn: 'git/verify@v1', next: 'end' },
          { id: 'end', type: 'end', status: 'SUCCESS' },
        ] } } };
        return { result: { status: 'ok', yaml: 'name: codeur@v2', data } };
      }
      return { result: {} };
    });
    const ctx = { api: { post }, t: (k: string) => k };
    const { container } = render(<GrapheAgentPanel ctx={ctx} />);
    await waitFor(() => screen.getByText(/codeur@v2/));
    fireEvent.click(screen.getByText(/codeur@v2/));
    await waitFor(() => expect(container.querySelector('.react-flow__node')).toBeTruthy());
    // Plie : seuls les steps top-level sont visibles (pas d'id "work/…").
    expect(container.querySelector('[data-id="work/body/do"]')).toBeNull();
    // Déplie tout (boucle `work` avec body → sous-nœuds).
    fireEvent.click(screen.getByTitle('Tout déplier'));
    // Les sous-nœuds aplatis apparaissent (positions absolues, pas de parentId).
    await waitFor(() => expect(container.querySelector('[data-id="work/body/do"]')).toBeTruthy());
    expect(container.querySelector('[data-id="work/condition"]')).toBeTruthy();
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

  it('affiche le YAML inline (mode compact, distinct du YAML classique)', async () => {
    const { ctx } = mockApi();
    render(<GrapheAgentPanel ctx={ctx} />);
    await waitFor(() => screen.getByText(/codeur@v2/));
    fireEvent.click(screen.getByText(/codeur@v2/));
    await waitFor(() => screen.getByText(/codeur@v2/));
    fireEvent.click(screen.getByText('YAML inline'));
    // Le mode inline affiche le marqueur + le contenu en LIGNES.
    await waitFor(() => expect(screen.getByText(/YAML inline.*\d+ chars/)).toBeTruthy());
    expect(screen.getByText(/role: codeur/)).toBeTruthy();
  });

  it('FSM boxed : affiche les agents comme des boxes dépliables', async () => {
    const { ctx } = mockApi();
    const { container } = render(<GrapheAgentPanel ctx={ctx} />);
    await waitFor(() => screen.getByText(/codeur@v2/));
    fireEvent.click(screen.getByText('FSM boxed'));
    // Le graphe boxed charge l'agent et l'affiche comme un nœud dépliable.
    await waitFor(() => expect(container.querySelector('.react-flow__node')).toBeTruthy());
    // Chaque agent est une box (data-id = nom de l'agent).
    await waitFor(() => expect(container.querySelector('[data-id="codeur@v2"]')).toBeTruthy());
    // Le nœud agent est un container : déplier révèle son FSM (sous-nœuds
    // préfixés).
    fireEvent.click(screen.getByTitle('Tout déplier'));
    await waitFor(() => expect(container.querySelector('[data-id="codeur@v2/pick"]')).toBeTruthy());
  });

  it('Taskflow : les boutons sont Zip all / Unzip all (au lieu de Tout déplier/replier)', async () => {
    const { ctx } = mockApi();
    const { container } = render(<GrapheAgentPanel ctx={ctx} />);
    await waitFor(() => screen.getByText(/codeur@v2/));
    fireEvent.click(screen.getByText(/codeur@v2/));
    fireEvent.click(screen.getByText('panels.graphe-agent.taskflow'));
    await waitFor(() => expect(container.querySelector('.react-flow')).toBeTruthy());
    // Les boutons Zip all / Unzip all sont présents.
    expect(screen.getByTitle('Zip all')).toBeTruthy();
    expect(screen.getByTitle('Unzip all')).toBeTruthy();
    // Et les boutons FSM ont disparu.
    expect(screen.queryByTitle('Tout déplier')).toBeNull();
    expect(screen.queryByTitle('Tout replier')).toBeNull();
  });

  it('FSM : utilise le graphe du BACKEND (graphe_utile/yaml_to_fsm)', async () => {
    const post = vi.fn(async (route: string) => {
      if (route === 'catalogue/agents/list') {
        return { result: { agents: [{ name: 'codeur@v2', role: 'codeur', description: 'x' }] } };
      }
      if (route === 'catalogue/agents/get') {
        return { result: { status: 'ok', yaml: 'name: codeur@v2', data: { role: 'codeur', entrypoints: { main: { steps: [{ id: 'x', type: 'end', status: 'SUCCESS' }] } } } } };
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
    const { container } = render(<GrapheAgentPanel ctx={ctx} />);
    await waitFor(() => screen.getByText(/codeur@v2/));
    fireEvent.click(screen.getByText(/codeur@v2/));
    // Le graphe vient du backend (nœud BACKEND_ONLY + tag tok_out hérité).
    await waitFor(() => expect(container.querySelector('[data-id="BACKEND_ONLY"]')).toBeTruthy());
    expect(post).toHaveBeenCalledWith('graphe_utile/yaml_to_fsm', { name: 'codeur@v2' });
  });
});

describe('agentYamlToGraph — FSM hiérarchique', () => {
  it('génère le graphe agent (steps + nœuds dépliables avec vars.inner)', () => {
    const data = { entrypoints: { main: { steps: [
      { id: 'pick', type: 'call', fn: 'workspace/token_task_pick@v1', next: 'work' },
      { id: 'work', type: 'while', next: 'after', condition: 'x<3',
        body: { steps: [
          { id: 'do', type: 'llm_call', next: 'chk' },
          { id: 'chk', type: 'switch', variable: 'st',
            conditions: [{ operator: 'EQUALS', value: 'done', next: 'fin' }],
            default: 'do' },
          { id: 'fin', type: 'set_variable', next: 'bd' },
          { id: 'bd', type: 'break' },
        ] } },
      { id: 'after', type: 'call', fn: 'git/verify@v1', next: 'end' },
      { id: 'end', type: 'end', status: 'SUCCESS' },
    ] } },
    };
    const g = agentYamlToGraph(data, 'x');
    const ids = g.nodes.map((n: any) => n.id);
    const tos = new Set(g.edges.map((e: any) => e.to));
    // Steps top-level présents
    expect(ids).toContain('pick');
    expect(ids).toContain('work');
    expect(ids).toContain('after');
    expect(ids).toContain('end');
    // entrypoint = pick (1er step) ; exitpoint = end
    expect(g.nodes.find((n: any) => n.id === 'pick').type).toBe('skill');
    expect(g.nodes.find((n: any) => n.id === 'end').type).toBe('exitpoint');
    // La boucle et le skill sont DÉPLIABLES (vars.inner)
    const work = g.nodes.find((n: any) => n.id === 'work');
    expect(work.vars.inner).toBeDefined();
    expect(work.vars.inner.nodes.length).toBeGreaterThan(0);
    // tags hérités : pick → token_eat
    expect(g.nodes.find((n: any) => n.id === 'pick').tags).toContain('token_eat');
  });

  it('incline les skills référencées (skillsMap) : dépliage + badge basic_skill', () => {
    const data = { entrypoints: { main: { steps: [
      { id: 'finalisation', type: 'call', fn: 'git/end_exec@v1', next: 'end',
        inputs: { project_id: '{{repo_eff}}' } },
      { id: 'end', type: 'end', status: 'SUCCESS' },
    ] } } };
    const skillsMap = {
      'git/end_exec@v1': {
        name: 'git/end_exec@v1',
        description: 'Termine le travail (commit + push).',
        inputs: { project_id: { type: 'string', required: true } },
        outputs: { commit_hash: { type: 'string' } },
        implementation: { type: 'python', function: 'git.end_exec' },
      },
    };
    const g = agentYamlToGraph(data, 'x', skillsMap);
    const step = g.nodes.find((n: any) => n.id === 'finalisation');
    // Skill python → badge « skill basique » sur le step call ET le nœud interne.
    expect(step.tags).toContain('basic_skill');
    // Le graphe représente l'AUTOMATE : les inputs du step call ne sont PAS
    // des états du flux (juste le nœud skill interne).
    const innerIds = step.vars.inner.nodes.map((n: any) => n.id);
    expect(innerIds).toEqual(['finalisation/skill']);
    // Le nœud skill interne porte la description (1 ligne) + le badge.
    const skillNode = step.vars.inner.nodes.find((n: any) => n.type === 'skill');
    expect(skillNode.tags).toContain('basic_skill');
    expect(skillNode.label).toContain('git/end_exec@v1');
    expect(skillNode.label).toContain('Termine le travail');
    // Contrat de la skill : input (entrypoint) → output (exitpoint), un seul
    // nœud chacun avec les champs en lignes.
    expect(skillNode.vars?.inner).toBeDefined();
    const contractIds = skillNode.vars.inner.nodes.map((n: any) => n.id);
    expect(contractIds).toEqual([`${skillNode.id}/in`, `${skillNode.id}/out`]);
    const inputsNode = skillNode.vars.inner.nodes.find((n: any) => n.id === `${skillNode.id}/in`);
    expect(inputsNode.label).toContain('project_id');
    expect(inputsNode.label).toContain('string');
    const outputsNode = skillNode.vars.inner.nodes.find((n: any) => n.id === `${skillNode.id}/out`);
    expect(outputsNode.label).toContain('commit_hash');
  });
});

describe('validateFsmGraph — règle entrée/sortie', () => {
  it('signalement un nœud sans entrée (hors entrypoint) et sans sortie (hors exit)', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'entrypoint' },  // entrée ok (entrypoint)
        { id: 'B', type: 'skill' },        // pas d'entrée ni sortie → 2 violations
        { id: 'C', type: 'exitpoint' },    // sortie ok (exitpoint)
      ],
      edges: [{ from: 'A', to: 'C' }],
    };
    const v = validateFsmGraph(g);
    expect(v.some((x: any) => x.id === 'B' && x.kind === 'no_in')).toBe(true);
    expect(v.some((x: any) => x.id === 'B' && x.kind === 'no_out')).toBe(true);
    // A et C ne doivent pas violer
    expect(v.filter((x: any) => x.id !== 'B').length).toBe(0);
  });

  it('greedy-coder est valide (aucune violation)', () => {
    const data = {
      entrypoints: { main: { steps: [
        { id: 'pick', type: 'call', fn: 'workspace/token_task_pick@v1', next: 'work' },
        { id: 'work', type: 'while', next: 'after',
          body: { steps: [
            { id: 'do', type: 'llm_call', next: 'chk' },
            { id: 'chk', type: 'switch',
              conditions: [{ operator: 'EQUALS', value: 'x', next: 'fin' }],
              default: 'do' },
            { id: 'fin', type: 'set_variable', next: 'bd' },
            { id: 'bd', type: 'break' },
          ] } },
        { id: 'after', type: 'call', fn: 'git/verify@v1', next: 'end' },
        { id: 'end', type: 'end', status: 'SUCCESS' },
      ] } },
    };
    const g = agentYamlToGraph(data, 'x');
    const v = validateFsmGraph(g);
    expect(v).toEqual([]);
  });
});

describe('agentYamlToTaskflow', () => {  it('suit le FSM + greffe les tokens (pick coding → end_exec → code_review)', () => {
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
    // C'est le FSM : steps = skill/flow/exitpoint + tokens greffés.
    expect(types).toContain('skill');      // pick (token_task_pick)
    expect(types).toContain('flow');       // exec_loop (while)
    expect(types).toContain('exitpoint');  // end
    // do_work (llm_call) est dans le sous-graphe de la boucle (hiérarchique).
    const loop = g.nodes.find((n: any) => n.id === 'exec_loop');
    const loopTypes = loop.vars.inner.nodes.map((n: any) => n.type);
    expect(loopTypes).toContain('llm');
    // Token-in : coding (type pioché)
    expect(types).toContain('token-in');
    expect(ids).toContain('coding');
    // Token-out : code_review (transition produite par post/end_exec)
    expect(types).toContain('token-out');
    expect(ids).toContain('code_review');
    // post est dans le body de la boucle → id FSM `exec_loop/body/post`
    const crEdge = g.edges.find((e: any) => e.to === 'code_review');
    expect(crEdge.from).toBe('exec_loop/body/post');
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
    expect(crEdge.from).toBe('do_work');
    // ask_verdict (workspace_verdict → task_verdict) produit done
    expect(ids).toContain('done');
    const doneEdges = g.edges.filter((e: any) => e.to === 'done');
    expect(doneEdges.length).toBeGreaterThan(0);
    // done vient d'un step llm_call (tool token), jamais du step end
    for (const e of doneEdges) {
      expect(e.from).not.toBe('end');
      expect(e.from).not.toBe('fail');
    }
    // PAS de done relié au step end (terminaison sans skill token)
    expect(g.edges.some((e: any) => e.from === 'end' && e.to === 'done')).toBe(false);
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
    expect(crEdge.from).toBe('finalisation');
    // release produit erreur_agent
    expect(ids).toContain('erreur_agent');
    const eaEdge = g.edges.find((e: any) => e.to === 'erreur_agent');
    expect(eaEdge.from).toBe('release');
  });

  it('résout le task_type par défaut quand le rôle est inconnu', () => {
    const data = { role: 'explore', entrypoints: { main: { steps: [
      { id: 'pick', type: 'call', fn: 'workspace/token_task_pick@v1',
        inputs: { task_types: '[{type: exploration, max_difficulty: expert}]' } },
    ] } } };
    const g = agentYamlToTaskflow(data, 'explore');
    expect(g.nodes.map((n: any) => n.id)).toContain('exploration');
  });

  it('prefixe les ids d\'un graphe (vues boxed, anti-collision entre agents)', () => {
    const g = {
      nodes: [
        { id: 'pick', type: 'skill', vars: { inner: {
          nodes: [{ id: 'pick/skill', type: 'skill', vars: {} }],
          edges: [{ from: 'pick/skill', to: 'pick/skill/in' }],
          entrypoint: 'pick/skill', exitpoints: ['pick/skill'],
        } } },
      ],
      edges: [{ from: 'pick', to: 'pick/skill' }],
    };
    prefixGraphIds(g, 'greedy-coder/');
    expect(g.nodes[0].id).toBe('greedy-coder/pick');
    expect(g.nodes[0].vars.inner.entrypoint).toBe('greedy-coder/pick/skill');
    expect(g.nodes[0].vars.inner.nodes[0].id).toBe('greedy-coder/pick/skill');
    expect(g.nodes[0].vars.inner.edges[0].from).toBe('greedy-coder/pick/skill');
    expect(g.edges[0].from).toBe('greedy-coder/pick');
  });
});

describe('greedy-coder réel — vérification du clipping', () => {
  it('le taskflow réel donne ≥ 20 zips via zip_all (et unzip_all revient à l\'état initial)', () => {
    const p = path.join(process.cwd(), '../../../../AgentsCatalogue/agents/greedy-coder.agent.yaml');
    const data = yamlParse(fs.readFileSync(p, 'utf8'));
    // Même construction que le panel en vue taskflow (transitions Pétri + tokens).
    const tf = buildTaskflowDoc(agentYamlToGraph(data, 'greedy-coder'), 'greedy-coder', data);
    const idsBefore = JSON.stringify(tf.nodes.map((n: any) => n.id).sort());
    const table = zipAll(tf);
    console.log(`[greedy-coder] zip_all → ${table.clips.length} clip(s)`);
    for (const c of table.clips) {
      console.log(`  - ${c.type} : on=[${c.on.join(',')}] zipped=[${c.zipped.join(',')}]${c.path?.length ? ` @${c.path.join('/')}` : ''}`);
    }
    expect(table.clips.length).toBeGreaterThanOrEqual(20);
    unzipAll(tf, table);
    expect(JSON.stringify(tf.nodes.map((n: any) => n.id).sort())).toBe(idsBefore);
    expect(tf.nodes.every((n: any) => n.vars?.visible !== false)).toBe(true);
  });
});

describe('check_fsm — règle entrée/sortie (error exclues en sortie, comptées en entrée)', () => {
  it('un nœud sans entrée NI sortie → 2 violations ; entrypoint/exitpoint exempts', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'entrypoint' },
        { id: 'B', type: 'skill' },
        { id: 'C', type: 'exitpoint' },
      ],
      edges: [{ from: 'A', to: 'C' }],
    };
    const v = check_fsm(g);
    expect(v.some((x: any) => x.id === 'B' && x.kind === 'no_in')).toBe(true);
    expect(v.some((x: any) => x.id === 'B' && x.kind === 'no_out')).toBe(true);
    expect(v.filter((x: any) => x.id !== 'B').length).toBe(0);
  });

  it('une sortie error ne satisfait PAS la sortie ; une entrée error satisfait l\'entrée', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'entrypoint' },
        { id: 'D', type: 'skill' },
        { id: 'E', type: 'skill' },
        { id: 'C', type: 'exitpoint' },
      ],
      edges: [
        { from: 'A', to: 'D', type: 'next' },
        { from: 'D', to: 'E', type: 'error' },   // sortie error : ne compte pas pour D
        { from: 'E', to: 'C', type: 'next' },
      ],
    };
    const v = check_fsm(g);
    // D n'a qu'une sortie error → no_out ; son entrée (A→D) est bonne.
    expect(v.some((x: any) => x.id === 'D' && x.kind === 'no_out')).toBe(true);
    expect(v.some((x: any) => x.id === 'D' && x.kind === 'no_in')).toBe(false);
    // E n'a qu'une entrée error (D→E) → comptée : pas de no_in ; sortie OK.
    expect(v.some((x: any) => x.id === 'E' && x.kind === 'no_in')).toBe(false);
    expect(v.some((x: any) => x.id === 'E' && x.kind === 'no_out')).toBe(false);
  });

  it('les branches d\'un switch sortent de la BOX (arête parente) et la cible a son entrée', () => {
    // Structure du builder : la box switch émet `sw → cible` au niveau parent
    // (PAS d'arête interne condition → cible). `target` a donc une entrée
    // visible, et aucune violation.
    const g = {
      nodes: [
        { id: 'main', type: 'entrypoint' },
        { id: 'sw', type: 'flow', vars: { inner: {
          nodes: [{ id: 'sw/condition', type: 'condition', tags: ['entrypoint'], vars: {} }],
          edges: [],
          entrypoint: 'sw/condition', exitpoints: ['sw/condition'],
        } } },
        { id: 'target', type: 'skill' },
        { id: 'end', type: 'exitpoint' },
      ],
      edges: [
        { from: 'main', to: 'sw', type: 'entry' },
        { from: 'sw', to: 'target', type: 'next' },
        { from: 'sw', to: 'end', type: 'next' },
        { from: 'target', to: 'end', type: 'next' },
      ],
    };
    const v = check_fsm(g);
    expect(v).toEqual([]);
  });

  it('cross_edge : une flèche sortante DANS la box est une violation', () => {
    const g = {
      nodes: [
        { id: 'box', type: 'flow', vars: { inner: {
          nodes: [{ id: 'box/x', type: 'step', vars: {} }],
          edges: [{ from: 'box/x', to: 'outside', type: 'next' }],   // sortie interne → externe
          entrypoint: 'box/x', exitpoints: ['box/x'],
        } } },
        { id: 'outside', type: 'skill' },
      ],
      edges: [{ from: 'outside', to: 'box', type: 'next' }],
    };
    const v = check_fsm(g);
    expect(v.some((x: any) => x.id === 'box/x→outside' && x.kind === 'cross_edge')).toBe(true);
  });

  it('dangling : une flèche vers un step INEXISTANT est une violation', () => {
    const g = {
      nodes: [
        { id: 'main', type: 'entrypoint' },
        { id: 'done', type: 'skill' },
      ],
      edges: [
        { from: 'main', to: 'done', type: 'entry' },
        { from: 'done', to: 'end', type: 'next' },   // `end` n'existe pas
      ],
    };
    const v = check_fsm(g);
    expect(v.some((x: any) => x.id === 'done→end' && x.kind === 'dangling')).toBe(true);
  });

  it('tous les agents réels du catalogue : aucune violation (régression)', () => {
    const dir = path.join(process.cwd(), '../../../../AgentsCatalogue/agents');
    const files = fs.readdirSync(dir).filter((f) => f.endsWith('.agent.yaml'));
    expect(files.length).toBeGreaterThan(0);
    const problems: string[] = [];
    for (const f of files) {
      const data = yamlParse(fs.readFileSync(path.join(dir, f), 'utf8'));
      const g = agentYamlToGraph(data, f.replace('.agent.yaml', ''));
      const v = check_fsm(g);
      if (v.length) {
        problems.push(`${f}: ${v.map((x: any) => `${x.id}(${x.kind})`).join(', ')}`);
      }
    }
    expect(problems).toEqual([]);
  });

  it('vue PLIÉE (hiérarchique) : aucun nœud sans entrée/sortie visible', () => {
    // Les sorties de box sont au niveau parent (switch) ou via les EXITPOINTS
    // (boucle : breaks = exitpoints, sortie distribuée au rendu). Un exitpoint
    // d'une box n'a pas besoin d'arête sortante interne.
    const dir = path.join(process.cwd(), '../../../../AgentsCatalogue/agents');
    const files = fs.readdirSync(dir).filter((f) => f.endsWith('.agent.yaml'));
    const isEntry = (x: any) => x.type === 'entrypoint' || (x.tags ?? []).includes('entrypoint');
    const isExit = (x: any) => x.type === 'exitpoint' || x.type === 'exit_error' || (x.tags ?? []).includes('exitpoint');
    const countOrphans = (g: any): string[] => {
      const out: string[] = [];
      const walk = (nodes: any[], edges: any[], exitpoints: Set<string>) => {
        const ins = new Set(edges.map((e: any) => e.to));
        const outs = new Set(edges.filter((e: any) => e.type !== 'error').map((e: any) => e.from));
        for (const n of nodes) {
          if (isEntry(n) || isExit(n)) continue;
          if (!ins.has(n.id)) out.push(`${n.id}:no_in`);
          if (!outs.has(n.id) && !exitpoints.has(n.id)) out.push(`${n.id}:no_out`);
          if (n.vars?.inner?.nodes) {
            walk(n.vars.inner.nodes, n.vars.inner.edges ?? [], new Set(n.vars.inner.exitpoints ?? []));
          }
        }
      };
      walk(g.nodes, g.edges ?? [], new Set());
      return out;
    };
    const problems: string[] = [];
    for (const f of files) {
      const data = yamlParse(fs.readFileSync(path.join(dir, f), 'utf8'));
      const g = agentYamlToGraph(data, f.replace('.agent.yaml', ''));
      const o = countOrphans(g);
      if (o.length) problems.push(`${f}: ${o.join(', ')}`);
    }
    expect(problems).toEqual([]);
  });
});
