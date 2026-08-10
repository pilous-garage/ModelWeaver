// Comparaison V2 (graphe Python) vs V1 (TS) — nb de nœuds tout déplié.
import { describe, it, expect } from 'vitest';
import fs from 'fs';
import { parse as yamlParse } from 'yaml';
import { agentYamlToGraph } from '../panels/graphe-agent.panel.tsx';
import { buildExpandedGraph, pruneInvisible } from '../theme_graphe/graphExpand.ts';
import { loadTheme } from '../theme_graphe/themeGraphe.ts';

const ROOT = process.cwd() + '/../../../../AgentsCatalogue/agents';

function allExpandedIds(g: any): number {
  const all = new Set<string>();
  const collect = (nodes: any[]) => { for (const n of nodes) { if (n.vars?.inner?.nodes?.length) { all.add(n.id); collect(n.vars.inner.nodes); } } };
  collect(g.nodes);
  const r = buildExpandedGraph(pruneInvisible(g), { theme: loadTheme(null), expanded: all, onToggle: () => {}, hideBoxFold: true });
  return r.nodes.length;
}

describe('V2 (Python) vs V1 (TS) — tout déplié', () => {
  it('greedy-coder : nb de nœuds rendus', () => {
    const data = yamlParse(fs.readFileSync(ROOT + '/greedy-coder.agent.yaml', 'utf8'));
    const v1 = agentYamlToGraph(data, 'greedy-coder');
    const v2 = JSON.parse(fs.readFileSync('/tmp/greedy-coder.fsm.json', 'utf8'));
    const n1 = allExpandedIds(v1);
    const n2 = allExpandedIds(v2);
    console.log(`V1 (TS): ${n1} nœuds rendus | V2 (Python): ${n2} nœuds rendus`);
    expect(Math.abs(n1 - n2)).toBeLessThanOrEqual(5);
  });
});
