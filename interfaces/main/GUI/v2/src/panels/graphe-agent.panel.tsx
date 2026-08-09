// graphe/agent — catalogue des agents + visualisation en YAML ou en graphe.
// Lecture seule (pas d'édition). Utilise graphe_subpanel (module générique).
// Sources : catalogue/agents/list + catalogue/agents/get.

import React, { useState, useEffect } from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';
import { GrapheSubPanel } from '../theme_graphe/graphe_subpanel.tsx';

const LANG_FR = `
panels:
  graphe-agent:
    titre: "Graphe Agent"
    agents: "Agents"
    yaml: "YAML"
    fsm: "FSM"
    taskflow: "Taskflow"
    erreur: "Erreur"
    selection: "Sélectionnez un agent"
`;

const LANG_EN = `
panels:
  graphe-agent:
    titre: "Agent Graph"
    agents: "Agents"
    yaml: "YAML"
    fsm: "FSM"
    taskflow: "Taskflow"
    erreur: "Error"
    selection: "Select an agent"
`;


// Convertit le YAML d'un agent (entrypoints.main.steps) en GraphDoc (vue FSM).
function agentYamlToGraph(data: any, name: string): any {
  const steps: any[] = data?.entrypoints?.main?.steps ?? [];
  if (steps.length === 0) {
    return { id: `agent-${name}`, title: name, nodes: [], edges: [] };
  }
  const nodes = steps.map((s: any) => ({
    id: s.id,
    type: s.type === 'end' ? (s.status === 'FAILED' ? 'exit' : 'end') : 'call',
    label: s.id,
    ref: s.fn ?? '',
    tags: s.on_error ? ['error-branch'] : [],
    vars: {},
  }));
  const edges: any[] = [];
  for (const s of steps) {
    if (s.next) edges.push({ from: s.id, to: s.next, label: 'next', type: 'next' });
    if (s.on_error) edges.push({ from: s.id, to: s.on_error, label: 'err', type: 'error' });
  }
  return { id: `agent-${name}`, title: name, nodes, edges };
}

// Vue TASKFLOW : le graphe des tokens consommés/produits.
// Chaque step qui consomme (task_claim_next) → token ENTRÉE (ref du rôle).
// Chaque step qui relâche (task_done, wait_for, git_push) → token SORTIE.
// C'est la « boîte noire » : l'agent au centre, les tokens entrent/sortent.
function agentYamlToTaskflow(data: any, name: string): any {
  const steps: any[] = data?.entrypoints?.main?.steps ?? [];
  const nodes: any[] = [
    { id: 'agent', type: 'agent', label: name, ref: name, tags: ['self'], vars: {} },
  ];
  const edges: any[] = [];

  // Déduire les tokens consommés (entrypoints) et produits (exit points).
  const consumed = new Set<string>();
  const produced = new Set<string>();
  for (const s of steps) {
    const fn: string = s.fn ?? '';
    if (fn.includes('task_claim')) {
      const role = s.inputs?.role_required || 'task';
      consumed.add(role);
      // arête : token → agent
      edges.push({ from: role, to: 'agent', label: 'consomme', type: 'token' });
    }
    if (fn.includes('issue') && (s.type === 'call')) consumed.add('issue');
    if (fn.includes('task_done')) {
      produced.add('done');
      edges.push({ from: 'agent', to: 'done', label: 'done', type: 'success' });
    }
    if (fn.includes('wait_for')) {
      produced.add('wait_for');
      edges.push({ from: 'agent', to: 'wait_for', label: 'sleep', type: 'token' });
    }
    if (fn.includes('git_push') || fn.includes('git_commit')) {
      produced.add('code');
      edges.push({ from: 'agent', to: 'code', label: 'code', type: 'success' });
    }
    if (fn.includes('task_create')) {
      produced.add('task');
      edges.push({ from: 'agent', to: 'task', label: 'task', type: 'token' });
    }
    if (s.type === 'end' && s.status === 'FAILED') {
      produced.add('fail');
      edges.push({ from: 'agent', to: 'fail', label: 'fail', type: 'error' });
    }
  }
  // Nœuds tokens (entrées à gauche, sorties à droite).
  for (const t of consumed) {
    nodes.push({ id: t, type: 'entry', label: t, ref: t, tags: ['token-in'], vars: {} });
  }
  for (const t of produced) {
    nodes.push({ id: t, type: 'exit', label: t, ref: t, tags: ['token-out'], vars: {} });
  }
  return { id: `taskflow-${name}`, title: `${name} — taskflow`, nodes, edges };
}


export function GrapheAgentPanel({ ctx }: { ctx: any }) {
  const { data } = usePoll<any>(
    ctx.api.post, 'catalogue/agents/list', {}, 10000,
    (res) => res?.result ?? {}, true,
  );
  const agents: any[] = data?.agents ?? [];
  const [sel, setSel] = useState<string>('');
  const [view, setView] = useState<'yaml' | 'fsm' | 'taskflow'>('fsm');
  const [agentData, setAgentData] = useState<any>(null);
  const [agentYaml, setAgentYaml] = useState('');
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!sel) { setAgentData(null); setAgentYaml(''); return; }
    setErr(null);
    ctx.api.post('catalogue/agents/get', { name: sel }).then((res: any) => {
      const r = res?.result ?? res ?? {};
      if (r.status === 'error') { setErr(r.error); setAgentData(null); return; }
      setAgentYaml(r.yaml ?? '');
      setAgentData(r.data ?? null);
    }).catch((e: any) => setErr(String(e?.message ?? e)));
  }, [sel, ctx.api.post]);

  const graphFsm = agentData ? agentYamlToGraph(agentData, sel) : null;
  const graphTaskflow = agentData ? agentYamlToTaskflow(agentData, sel) : null;
  const shownGraph = view === 'taskflow' ? graphTaskflow : graphFsm;

  return (
    <div style={{ height: '100%', display: 'flex', boxSizing: 'border-box', fontSize: 12 }}>
      {/* Catalogue d'agents */}
      <div style={{ width: 180, minWidth: 180, borderRight: '1px solid var(--mw-border, #1e293b)', overflow: 'auto', padding: 6 }}>
        <div style={{ fontWeight: 700, margin: '4px 0', color: '#a5b4fc' }}>
          {ctx.t?.('panels.graphe-agent.agents') ?? 'Agents'} ({agents.length})
        </div>
        {agents.map((a) => (
          <div
            key={a.name}
            onClick={() => setSel(a.name)}
            style={{ padding: '3px 6px', cursor: 'pointer', borderRadius: 4, fontSize: 11,
                     background: sel === a.name ? 'rgba(56,189,248,.15)' : 'transparent',
                     color: sel === a.name ? '#38bdf8' : '#cbd5e1' }}
          >
            {a.name}
          </div>
        ))}
      </div>

      {/* Détail */}
      <div style={{ flex: 1, minWidth: 0, padding: 6, display: 'flex', flexDirection: 'column' }}>
        {!sel && <div style={{ color: '#475569', padding: 20, textAlign: 'center' }}>
          {ctx.t?.('panels.graphe-agent.selection') ?? 'Sélectionnez un agent'}
        </div>}
        {err && <div style={{ color: '#f87171', marginBottom: 4 }}>{err}</div>}
        {sel && agentData && (
          <>
            {/* Onglets FSM / Taskflow / YAML */}
            <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
              <button className="mw-btn" onClick={() => setView('fsm')}
                style={{ padding: '2px 10px', fontSize: 11, opacity: view === 'fsm' ? 1 : 0.5 }}>
                {ctx.t?.('panels.graphe-agent.fsm') ?? 'FSM'}
              </button>
              <button className="mw-btn" onClick={() => setView('taskflow')}
                style={{ padding: '2px 10px', fontSize: 11, opacity: view === 'taskflow' ? 1 : 0.5 }}>
                {ctx.t?.('panels.graphe-agent.taskflow') ?? 'Taskflow'}
              </button>
              <button className="mw-btn" onClick={() => setView('yaml')}
                style={{ padding: '2px 10px', fontSize: 11, opacity: view === 'yaml' ? 1 : 0.5 }}>
                {ctx.t?.('panels.graphe-agent.yaml') ?? 'YAML'}
              </button>
            </div>
            {view !== 'yaml' ? (
              <GrapheSubPanel doc={shownGraph} editable={false} engine="reactflow" height="100%" />
            ) : (
              <pre style={{ flex: 1, minHeight: 0, overflow: 'auto', margin: 0, fontSize: 10,
                           background: 'rgba(15,23,42,.5)', padding: 8, borderRadius: 6,
                           border: '1px solid var(--mw-border, #1e293b)', color: '#cbd5e1' }}>
                {agentYaml}
              </pre>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export const Panel: PanelDef = {
  id: 'graphe-agent',
  labelKey: 'panels.graphe-agent.titre',
  iconKey: 'panels.graphe-agent.titre',
  version: '0.1.0',
  essential: false,
  bundles: ['graphe'],
  paramsSchema: {},
  defaultParams: {},
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[graphe-agent] Catalogue agents + FSM / Taskflow / YAML',
  component: GrapheAgentPanel,
};

export const langFr = LANG_FR;
