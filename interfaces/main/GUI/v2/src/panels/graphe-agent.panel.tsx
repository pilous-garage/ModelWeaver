// graphe/agent — catalogue des agents + visualisation en YAML ou en graphe.
// Lecture seule (pas d'édition). Utilise graphe_subpanel (module générique).
// Sources : catalogue/agents/list + catalogue/agents/get.

import React, { useState, useEffect, useMemo } from 'react';
import { parse as parseYaml } from 'yaml';
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
    theme: "Thème"
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
    theme: "Theme"
`;


// Valide le graphe FSM selon la règle :
//   - un nœud doit avoir une arête ENTRANTE, OU être l'entrypoint (1er step)
//   - un nœud doit avoir une arête SORTANTE, OU être un exitpoint (end)
// Retourne la liste des violations {id, kind: 'no_in'|'no_out'}.
export function validateFsmGraph(graph: any, isExit: (id: string) => boolean = () => false): any[] {
  const nodes: any[] = graph?.nodes ?? [];
  const edges: any[] = graph?.edges ?? [];
  const incoming = new Set(edges.map((e: any) => e.to));
  const outgoing = new Set(edges.map((e: any) => e.from));
  const violations: any[] = [];
  for (const n of nodes) {
    const tags = n.tags ?? [];
    const isEntry = n.type === 'entrypoint' || tags.includes('entrypoint');
    const isExit = n.type === 'exitpoint' || n.type === 'exit_error' || tags.includes('exitpoint');
    if (!isEntry && !incoming.has(n.id)) {
      violations.push({ id: n.id, kind: 'no_in' });
    }
    if (!isExit && !outgoing.has(n.id)) {
      violations.push({ id: n.id, kind: 'no_out' });
    }
  }
  return violations;
}

// Convertit le YAML d'un agent en GraphDoc (vue FSM) — via le modèle
// hiérarchique createFsmSubGraphFrom(agent). Chaque step/skill/loop/agent
// est dépliable ; les arêtes connectent les points atomiques, et les
// entrypoint/exitpoints sont des demi-arêtes redirigées quand on déplie.
import { createFsmSubGraphFrom, type FsmComponent } from '../theme_graphe/fsmHierarchy.ts';

function yamlStepToComponent(s: any, kind: FsmComponent['kind'] = 'step'): FsmComponent {
  return {
    kind: s.type === 'switch' || s.type === 'if' ? (s.type as any)
      : (s.type === 'while' || s.type === 'for') ? 'loop'
      : s.type === 'call' && s.fn ? 'skill'
      : 'step',
    id: s.id,
    label: s.id,
    fn: s.fn,
    type: s.type,
    status: s.status,
    variable: s.variable,
    conditions: s.conditions,
    default: s.default,
    condition: s.condition,
    body: s.body ? { steps: (s.body.steps ?? []).map((b: any) => yamlStepToComponent(b)) } : undefined,
    next: s.next,
    on_error: s.on_error,
  };
}

function yamlAgentToComponent(data: any, name: string): FsmComponent {
  const steps = data?.entrypoints?.main?.steps ?? [];
  return {
    kind: 'agent',
    id: name,
    label: name,
    body: { steps: steps.map((s: any) => yamlStepToComponent(s)) },
  };
}

export function agentYamlToGraph(data: any, name: string): any {
  if (!data?.entrypoints?.main?.steps?.length) {
    return { id: `agent-${name}`, title: name, nodes: [], edges: [] };
  }
  const agent = yamlAgentToComponent(data, name);
  const sg = createFsmSubGraphFrom(agent);
  return { id: `agent-${name}`, title: name, nodes: sg.nodes, edges: sg.edges };
}

// Vue TASKFLOW : analyse step-by-step du graphe de l'agent.
// Chaque step du FSM devient un nœud ; les TOKENS de tâches (task_type) sont
// des nœuds token-in (consommés) / token-out (produits) reliés aux steps qui
// les piochent, les créent ou les transitionnent.
// - token_task_pick   : token-in (le type pioché) + token-out (même type, doing)
// - token_task_create : token-out (le type créé)
// - token_task_modify : token-in (type courant) → token-out (nouveau type)
//   ex. coding → code_review (productions obligatoires) ; verdict to_difficult
//   → 2 sorties possibles (bump difficulty | re-découpe).
export function agentYamlToTaskflow(data: any, name: string): any {
  const steps: any[] = data?.entrypoints?.main?.steps ?? [];
  const nodes: any[] = [];
  const edges: any[] = [];
  // Tokens vus : id → {type (token-in|token-out), label}
  const tokenNodes = new Map<string, string>();
  const stepNodes = new Map<string, any>();

  const role = data?.role ?? '';
  const roleToTask: Record<string, string> = {
    codeur: 'coding', relecteur: 'code_review', test_runner: 'testing_code',
    explore: 'exploration', planificateur: 'analysis', architecte: 'analysis',
    orchestrateur: 'merger_code',
  };
  const taskRole = roleToTask[role] ?? role;

  // Transitions connues du pipeline (task_type courant → étape suivante).
  // Utilisées pour déduire les tokens que le LLM peut produire via ses tools
  // (end_exec / token_task_modify) pendant un step llm_call.
  const pipelineNext: Record<string, string> = {
    coding: 'code_review',
    code_review: 'merger_code',
    merger_code: 'testing_code',
    testing_code: 'done',
    analysis: 'merge_split',
    merge_split: 'done',
    exploration: 'done',
  };

  // Bundles connus qui exposent des tools de production de tokens au LLM.
  const bundleTokenTools: Record<string, string[]> = {
    dev: ['end_exec', 'token_task_create', 'token_task_modify',
          'token_task_release', 'task_verdict'],
    workspace_verdict: ['end_exec', 'token_task_modify', 'task_verdict', 'task_done'],
    coding_work: ['it_is_done', 'exit_loop_too_hard', 'making_progress'],
  };

  // Productions spécifiques d'un tool (token produit).
  const toolProductions: Record<string, string> = {
    token_task_release: 'erreur_agent',
    exit_loop_too_hard: 'too_hard',
    task_verdict: 'done',
    task_done: 'done',
  };

  const resolveType = (v: any, dflt: string): string => {
    const s = String(v ?? '').trim();
    if (!s || s.includes('{{')) return dflt || taskRole;
    return s;
  };

  const ensureToken = (tid: string, kind: 'token-in' | 'token-out', label: string) => {
    if (!tokenNodes.has(tid)) {
      tokenNodes.set(tid, kind);
      nodes.push({ id: tid, type: kind, label, ref: label, tags: [kind], vars: {} });
    }
  };

  // Ajoute une production token depuis un step (si le token n'existe pas déjà).
  const addProduction = (fromId: string, tt: string, label: string) => {
    ensureToken(tt, 'token-out', tt);
    edges.push({ from: fromId, to: tt, label, type: 'success' });
  };

  const stepId = (s: any, parent: string): string =>
    `${parent}_${s.id ?? 'step'}`;

  // Parcourt les steps (top-level + corps de boucle), crée un nœud par step.
  const walk = (stepList: any[], parent: string, prevId: string | null): string | null => {
    let last = prevId;
    for (const s of stepList) {
      const id = stepId(s, parent);
      const fn: string = s.fn ?? '';
      const stype = s.type ?? 'call';
      const kind = stype === 'llm_call' ? 'llm' : stype === 'end' ? 'end' : 'call';
      const label = s.id ?? id;
      stepNodes.set(id, s);
      nodes.push({
        id, type: kind, label, ref: fn || stype,
        tags: [fn || stype], vars: {},
      });
      if (last) edges.push({ from: last, to: id, label: 'next', type: 'next' });
      last = id;

      // ── TOKENS par skill ──
      if (fn === 'workspace/token_task_pick@v1') {
        // token-in : le(s) type(s) pioché(s) ; token-out : le type (doing)
        const raw = String(s.inputs?.task_types ?? '');
        const m = raw.match(/type:\s*([\w]+)/);
        const tt = m ? m[1] : taskRole;
        ensureToken(tt, 'token-in', tt);
        edges.push({ from: tt, to: id, label: 'pick', type: 'token' });
        ensureToken(tt, 'token-out', `${tt}→doing`);
        edges.push({ from: id, to: `${tt}→doing`, label: 'doing', type: 'success' });
      } else if (fn === 'workspace/token_task_create@v1') {
        const raw = String(s.inputs?.task?.task_type ?? s.inputs?.task_type ?? '');
        const m = raw.match(/([\w]+)/);
        const tt = m ? m[1] : 'task';
        ensureToken(tt, 'token-out', tt);
        edges.push({ from: id, to: tt, label: 'create', type: 'token' });
      } else if (fn === 'workspace/token_task_modify@v1') {
        // token-in : type courant (implicite, le token de l'agent) ;
        // token-out : le nouveau type (production obligatoire)
        const raw = String(s.inputs?.new_task_type ?? '');
        const m = raw.match(/([\w]+)/);
        const nt = m ? m[1] : 'code_review';
        ensureToken(taskRole, 'token-in', taskRole);
        edges.push({ from: taskRole, to: id, label: 'modify', type: 'token' });
        ensureToken(nt, 'token-out', nt);
        edges.push({ from: id, to: nt, label: '→' + nt, type: 'success' });
      } else if (fn.includes('token_task_pick') || fn.includes('task_claim')) {
        const tt = resolveType(s.inputs?.role_required ?? s.inputs?.role, taskRole);
        ensureToken(tt, 'token-in', tt);
        edges.push({ from: tt, to: id, label: 'pick', type: 'token' });
      } else if (fn.includes('end_exec')) {
        // end_exec : transition du token courant → étape suivante du pipeline
        // (ex. coding → code_review). Production OBLIGATOIRE.
        const raw = String(s.inputs?.new_task_type ?? '');
        const m = raw.match(/([\w]+)/);
        const nt = m ? m[1] : (pipelineNext[taskRole] ?? 'code_review');
        ensureToken(taskRole, 'token-in', taskRole);
        edges.push({ from: taskRole, to: id, label: 'deliver', type: 'token' });
        addProduction(id, nt, '→' + nt);
      } else if (fn.includes('token_task_release')) {
        // release : libère le token + produit un token erreur_agent.
        ensureToken(taskRole, 'token-in', taskRole);
        edges.push({ from: taskRole, to: id, label: 'release', type: 'token' });
        addProduction(id, 'erreur_agent', 'erreur_agent');
      } else if (fn.includes('exit_loop_too_hard')) {
        // too_hard : bump difficulty ou re-découpe → token too_hard.
        addProduction(id, 'too_hard', 'too_hard');
      }
      // ── LLM_CALL : les tools exposés (bundles) peuvent produire des tokens.
      //    On déduit les productions possibles : end_exec/modify → étape
      //    suivante du pipeline ; task_verdict/task_done → done ;
      //    token_task_release → erreur_agent ; exit_loop_too_hard → too_hard.
      if (stype === 'llm_call') {
        const bundles: string[] = s.bundles ?? [];
        const tokenTools = new Set<string>();
        for (const b of bundles) {
          for (const t of (bundleTokenTools[b] ?? [])) tokenTools.add(t);
        }
        if (tokenTools.has('end_exec') || tokenTools.has('token_task_modify')) {
          const nt = pipelineNext[taskRole];
          if (nt && nt !== 'done') addProduction(id, nt, '→' + nt);
        }
        for (const tool of tokenTools) {
          const prod = toolProductions[tool];
          if (prod) addProduction(id, prod, prod);
        }
      }
      // Corps de boucle
      if (s.type === 'while' && s.body?.steps) {
        last = walk(s.body.steps, id, last);
      }
    }
    return last;
  };
  walk(steps, 'main', null);
  // Le token `done` est produit UNIQUEMENT par les tools LLM (task_verdict /
  // task_done / transition vers done) — pas automatiquement par un step `end`
  // (qui n'est qu'une terminaison, sans skill token).
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

  // Chargement de l'agent : fetch une fois par sélection.
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

  // Calcul en MÉMOIRE des deux graphes : recalculé UNIQUEMENT quand agentData
  // ou sel change (pas à chaque poll du catalogue → pas de blip/re-layout).
  const { graphFsm, graphTaskflow } = useMemo(() => {
    return {
      graphFsm: agentData ? agentYamlToGraph(agentData, sel) : null,
      graphTaskflow: agentData ? agentYamlToTaskflow(agentData, sel) : null,
    };
  }, [agentData, sel]);

  const shownGraph = view === 'taskflow' ? graphTaskflow : graphFsm;
  const violations = view === 'fsm' && graphFsm ? validateFsmGraph(graphFsm) : [];

  // ── Thème graphe : liste des thèmes (kind=graphe) + sélection ──
  const [graphThemes, setGraphThemes] = useState<string[]>([]);
  const [themeName, setThemeName] = useState<string>('basique');
  const [themeObj, setThemeObj] = useState<any>(null);

  useEffect(() => {
    ctx.api.post('theme/list', { kind: 'graphe' }).then((res: any) => {
      const r = res?.result ?? res ?? {};
      const names = (r.themes ?? []).map((t: any) => t.name);
      if (names.length) setGraphThemes(names);
    }).catch(() => {});
  }, [ctx.api.post]);

  useEffect(() => {
    if (!themeName) return;
    ctx.api.post('theme/get', { name: themeName, kind: 'graphe' }).then((res: any) => {
      const r = res?.result ?? res ?? {};
      if (r.yaml) {
        try { setThemeObj(parseYaml(r.yaml)); } catch { setThemeObj(null); }
      }
    }).catch(() => setThemeObj(null));
  }, [themeName, ctx.api.post]);

  const selectTheme = (e: any) => setThemeName(e.target.value);

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
            {/* Règle FSM : chaque nœud a une entrée (sauf entrypoint) et une
                sortie (sauf exitpoint). */}
            {violations.length > 0 && (
              <div style={{
                marginBottom: 6, padding: '4px 8px', borderRadius: 6, fontSize: 10,
                background: 'rgba(239,68,68,.12)', border: '1px solid #ef4444', color: '#fca5a5',
              }}>
                ⚠ FSM incomplet :{' '}
                {violations.map((v: any) => `${v.id} (${v.kind === 'no_in' ? 'sans entrée' : 'sans sortie'})`).join(', ')}
              </div>
            )}
            {/* Sélecteur de thème graphe */}
            {graphThemes.length > 1 && (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: 10, color: '#94a3b8' }}>{ctx.t?.('panels.graphe-agent.theme') ?? 'Thème'}:</span>
                <select className="mw-btn" value={themeName} onChange={selectTheme}
                  style={{ fontSize: 10, padding: '1px 6px' }}>
                  {graphThemes.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
            )}
            {view !== 'yaml' ? (
              <GrapheSubPanel doc={shownGraph} theme={themeObj} editable={false} engine="reactflow" height="100%" />
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
