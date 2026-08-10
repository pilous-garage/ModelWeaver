import { describe, it, expect } from 'vitest';
import fs from 'fs';
import path from 'path';
import { parse as yamlParse } from 'yaml';
import { buildTaskflowDoc, markAllVisible, check_petri, check_balance } from '../panels/taskflowBuild.ts';
import { agentYamlToGraph } from '../panels/graphe-agent.panel.tsx';

function makeData() {
  return { role: 'codeur', entrypoints: { main: { steps: [
    { id: 'pick', type: 'call', fn: 'workspace/token_task_pick@v1', next: 'work',
      inputs: { task_types: '[{type: coding}]' } },
    { id: 'work', type: 'while', next: 'end', condition: 'x<3',
      body: { steps: [
        { id: 'do', type: 'llm_call', next: 'chk' },
        { id: 'chk', type: 'switch', variable: 'st',
          conditions: [{ operator: 'EQUALS', value: 'done', next: 'bd' }],
          default: 'do' },
        { id: 'bd', type: 'break' },
      ] } },
    { id: 'fin', type: 'call', fn: 'git/end_exec@v1', next: 'end',
      inputs: { new_task_type: 'code_review' } },
    { id: 'end', type: 'end', status: 'SUCCESS' },
  ] } } };
}

describe('taskflowBuild — visible + tokens', () => {
  it('markAllVisible : TOUS les nœuds (y compris inner et transitions) sont visibles', () => {
    const doc = buildTaskflowDoc(agentYamlToGraph(makeData(), 'x'), 'x');
    const all = (nodes: any[]): any[] => nodes.flatMap((n) => [n, ...(n.vars?.inner?.nodes ? all(n.vars.inner.nodes) : [])]);
    const allNodes = all(doc.nodes);
    expect(allNodes.length).toBeGreaterThan(0);
    for (const n of allNodes) {
      expect(n.vars.visible).toBe(true);
    }
    // Il y a des transitions (insérées), elles sont visibles aussi.
    expect(allNodes.some((n) => n.type === 'transition')).toBe(true);
  });

  it('tokens : places + arêtes sur la transition de sortie, nœuds token visibles', () => {
    const doc = buildTaskflowDoc(agentYamlToGraph(makeData(), 'x'), 'x', makeData());
    // pick (consume coding) : place token:coding reliée à sa transition de sortie.
    const coding = doc.nodes.find((n: any) => n.id === 'token:coding');
    expect(coding).toBeTruthy();
    expect(coding.vars.visible).toBe(true);
    // La place coding alimente la transition de sortie de pick.
    const outT = doc.edges.filter((e: any) => e.from === 'pick').map((e: any) => e.to)
      .find((t: string) => doc.nodes.find((n: any) => n.id === t)?.type === 'transition');
    expect(doc.edges.some((e: any) => e.from === 'token:coding' && e.to === outT)).toBe(true);
    // fin (produce code_review) : place token:code_review produite par sa transition de sortie.
    const cr = doc.nodes.find((n: any) => n.id === 'token:code_review');
    expect(cr).toBeTruthy();
    const finOut = doc.edges.filter((e: any) => e.from === 'fin').map((e: any) => e.to)
      .find((t: string) => doc.nodes.find((n: any) => n.id === t)?.type === 'transition');
    expect(doc.edges.some((e: any) => e.from === finOut && e.to === 'token:code_review')).toBe(true);
  });
});

describe('check_petri — connectivité place/transition', () => {
  it('une PLACE sans sortie → no_out ; transitions connectées OK', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'entrypoint', tags: ['entrypoint'], vars: {} },
        { id: 't1', type: 'transition', vars: {} },
        { id: 'P', type: 'place', vars: {} },
        { id: 'C', type: 'exitpoint', tags: ['exitpoint'], vars: {} },
      ],
      edges: [
        { from: 'A', to: 't1', type: 'next' },
        { from: 't1', to: 'P', type: 'next' },
      ],
    };
    const v = check_petri(g);
    expect(v.some((x: any) => x.id === 'P' && x.kind === 'no_out')).toBe(true);
    expect(v.some((x: any) => x.id === 'P' && x.kind === 'no_in')).toBe(false);
    // t1 bien connectée, entrypoint/exitpoint exempts (C isolée ne viole pas).
    expect(v.some((x: any) => x.id === 't1')).toBe(false);
    expect(v.some((x: any) => x.id === 'A' || x.id === 'C')).toBe(false);
  });

  it('une TRANSITION sans entrée → no_in', () => {
    const g = {
      nodes: [
        { id: 't1', type: 'transition', vars: {} },
        { id: 'P', type: 'place', vars: {} },
        { id: 't2', type: 'transition', vars: {} },
        { id: 'C', type: 'exitpoint', tags: ['exitpoint'], vars: {} },
      ],
      edges: [
        { from: 't1', to: 'P', type: 'next' },
        { from: 'P', to: 't2', type: 'next' },
        { from: 't2', to: 'C', type: 'next' },
      ],
    };
    const v = check_petri(g);
    expect(v.some((x: any) => x.id === 't1' && x.kind === 'no_in')).toBe(true);
  });

  it('les TOKENS (token_task) sont exempts — une place token sans entrée ne viole pas', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'entrypoint', tags: ['entrypoint'], vars: {} },
        { id: 't1', type: 'transition', vars: {} },
        { id: 'tok', type: 'place', vars: { token: true } },
      ],
      edges: [
        { from: 'A', to: 't1', type: 'next' },
        { from: 't1', to: 'tok', type: 'token' },
      ],
    };
    const v = check_petri(g);
    expect(v.some((x: any) => x.id === 'tok' && x.kind === 'no_in')).toBe(false);
  });

  it('tous les agents réels : aucune violation (régression)', () => {
    const dir = path.join(process.cwd(), '../../../../AgentsCatalogue/agents');
    const files = fs.readdirSync(dir).filter((f) => f.endsWith('.agent.yaml'));
    expect(files.length).toBeGreaterThan(0);
    const problems: string[] = [];
    for (const f of files) {
      const data = yamlParse(fs.readFileSync(path.join(dir, f), 'utf8'));
      const tf = buildTaskflowDoc(agentYamlToGraph(data, f.replace('.agent.yaml', '')), f.replace('.agent.yaml', ''), data);
      const v = check_petri(tf);
      if (v.length) problems.push(`${f}: ${v.map((x: any) => `${x.id}(${x.kind})`).join(', ')}`);
    }
    expect(problems).toEqual([]);
  });
});

describe('check_balance — nb_arête == total_in == total_out', () => {
  it('un graphe équilibré ne rapporte rien', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'place', vars: {} },
        { id: 't', type: 'transition', vars: {} },
        { id: 'B', type: 'place', vars: {} },
      ],
      edges: [
        { from: 'A', to: 't', type: 'next' },
        { from: 't', to: 'B', type: 'next' },
      ],
    };
    expect(check_balance(g)).toEqual([]);
  });

  it('une arête vers un nœud INEXISTANT casse la balance (dangling)', () => {
    const g = {
      nodes: [
        { id: 'A', type: 'place', vars: {} },
        { id: 't', type: 'transition', vars: {} },
      ],
      edges: [
        { from: 'A', to: 't', type: 'next' },
        { from: 't', to: 'B', type: 'next' },   // B n'existe pas
      ],
    };
    const v = check_balance(g);
    expect(v.some((x: any) => x.kind === 'dangling_to')).toBe(true);
  });

  it('tous les agents réels : équilibrés (régression)', () => {
    const dir = path.join(process.cwd(), '../../../../AgentsCatalogue/agents');
    const files = fs.readdirSync(dir).filter((f) => f.endsWith('.agent.yaml'));
    const problems: string[] = [];
    for (const f of files) {
      const data = yamlParse(fs.readFileSync(path.join(dir, f), 'utf8'));
      const tf = buildTaskflowDoc(agentYamlToGraph(data, f.replace('.agent.yaml', '')), f.replace('.agent.yaml', ''), data);
      const b = check_balance(tf);
      if (b.length) problems.push(`${f}: ${JSON.stringify(b)}`);
    }
    expect(problems).toEqual([]);
  });
});
