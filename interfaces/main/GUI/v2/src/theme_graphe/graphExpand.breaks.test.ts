import { describe, it, expect } from 'vitest';
import fs from 'fs';
import path from 'path';
import { parse as yamlParse } from 'yaml';
import { agentYamlToGraph } from '../panels/graphe-agent.panel.tsx';
import { buildTaskflowDoc } from '../panels/taskflowBuild.ts';
import { buildExpandedGraph, pruneInvisible } from '../theme_graphe/graphExpand.ts';
import { loadTheme } from '../theme_graphe/themeGraphe.ts';

const ROOT = path.join(process.cwd(), '../../../../AgentsCatalogue/agents');

function greedyCoder() {
  const data = yamlParse(fs.readFileSync(path.join(ROOT, 'greedy-coder.agent.yaml'), 'utf8'));
  return { data, g: agentYamlToGraph(data, 'greedy-coder') };
}

describe('break_done a une sortie (vue FSM dépliée)', () => {
  it('break_done/break_hard → after_loop visibles (fan-out des exitpoints)', () => {
    const { g } = greedyCoder();
    const r = buildExpandedGraph(g, {
      algo: 'dagre', dir: 'LR', theme: loadTheme(null),
      expanded: new Set(['working_loop']), onToggle: () => {}, hideBoxFold: true,
    });
    expect(r.edges.some((e: any) => e.source === 'working_loop/body/break_done' && e.target === 'after_loop')).toBe(true);
    expect(r.edges.some((e: any) => e.source === 'working_loop/body/break_hard' && e.target === 'after_loop')).toBe(true);
  });

  it('loop : pas de nœud "false" artificiel — la sortie "false" va DIRECTEMENT à after_loop', () => {
    const { g } = greedyCoder();
    const wl = g.nodes.find((n: any) => n.id === 'working_loop');
    // exitpoints = condition + breaks (pas de condition/exit).
    expect(wl.vars.inner.exitpoints).toEqual([
      'working_loop/condition',
      'working_loop/body/break_done',
      'working_loop/body/break_hard',
    ]);
    expect(wl.vars.inner.nodes.some((n: any) => n.id === 'working_loop/condition/exit')).toBe(false);
    const r = buildExpandedGraph(g, {
      algo: 'dagre', dir: 'LR', theme: loadTheme(null),
      expanded: new Set(['working_loop']), onToggle: () => {}, hideBoxFold: true,
    });
    expect(r.nodes.some((n: any) => n.id === 'working_loop/condition/exit')).toBe(false);
    // true → do_work ; false → after_loop directement.
    expect(r.edges.some((e: any) => e.source === 'working_loop/condition' && e.target === 'working_loop/body/do_work')).toBe(true);
    expect(r.edges.some((e: any) => e.source === 'working_loop/condition' && e.target === 'after_loop')).toBe(true);
    // PAS d'id dupliqué (bug « deux enfants avec la même clé ») : chaque arête
    // fan-out a un id unique = source->cible réels.
    const ids = r.edges.map((e: any) => e.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(r.edges.find((e: any) => e.source === 'working_loop/body/break_done')?.id).toBe('working_loop/body/break_done->after_loop');
    expect(r.edges.find((e: any) => e.source === 'working_loop/body/break_hard')?.id).toBe('working_loop/body/break_hard->after_loop');
  });
});

describe('break_done a une sortie (vue taskflow/Pétri dépliée)', () => {
  it('chaîne break_done → t → after_loop complète', () => {
    const { data, g } = greedyCoder();
    const tf = buildTaskflowDoc(g, 'greedy-coder', data);
    const r = buildExpandedGraph(pruneInvisible(tf), {
      algo: 'dagre', dir: 'LR', theme: loadTheme(null),
      expanded: new Set(['working_loop']), onToggle: () => {}, hideBoxFold: true,
    });
    const t = r.edges.find((e: any) => e.source === 'working_loop/body/break_done')?.target;
    expect(t).toBeTruthy();
    // break_done → transition → after_loop : les 2 arêtes existent.
    expect(r.edges.some((e: any) => e.source === 'working_loop/body/break_done' && e.target === t)).toBe(true);
    expect(r.edges.some((e: any) => e.source === t && e.target === 'after_loop')).toBe(true);
  });

  it('le doc taskflow n\'a PAS d\'arête trans-frontière dans la box (breaks = exitpoints)', () => {
    const { data, g } = greedyCoder();
    const tf = buildTaskflowDoc(g, 'greedy-coder', data);
    const wl = tf.nodes.find((n: any) => n.id === 'working_loop');
    const levelIds = new Set(wl.vars.inner.nodes.map((n: any) => n.id));
    for (const e of wl.vars.inner.edges) {
      expect(levelIds.has(e.from) && levelIds.has(e.to)).toBe(true);
    }
    // break_done est un EXITPOINT de la boucle.
    expect(wl.vars.inner.exitpoints).toContain('working_loop/body/break_done');
  });
});
