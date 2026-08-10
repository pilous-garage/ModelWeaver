// graphe/agent — catalogue des agents + visualisation en YAML ou en graphe.
// Lecture seule (pas d'édition). Utilise graphe_subpanel (module générique).
// Sources : catalogue/agents/list + catalogue/agents/get.

import React, { useState, useEffect, useMemo } from 'react';
import type { PanelDef } from './contract.ts';
import { usePoll } from './panel-utils.ts';
import { GrapheSubPanel } from '../theme_graphe/graphe_subpanel.tsx';
import { useGraphThemeControl } from '../graphThemeStore.ts';
import { unfoldAllFsm, buildTaskflowDoc } from './taskflowBuild.ts';
import { zip_all, unzip_all } from './taskflowGraph.ts';

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
// Les arêtes des SOUS-GRAPHES (inner des boxes) comptent aussi : les branches
// d'un switch/loop relient leurs cibles même si elles ne sont pas au top, et une
// box (switch/loop) a une sortie dès que son inner a des branches sortantes.
// Retourne la liste des violations {id, kind: 'no_in'|'no_out'}.
export function validateFsmGraph(graph: any, isExit: (id: string) => boolean = () => false): any[] {
  const nodes: any[] = graph?.nodes ?? [];
  // Collecte récursive de toutes les arêtes (top + inner).
  const allEdges: any[] = [];
  const collectEdges = (ns: any[] | undefined, es: any[] | undefined) => {
    allEdges.push(...(es ?? []));
    for (const n of ns ?? []) {
      if (n.vars?.inner?.nodes) collectEdges(n.vars.inner.nodes, n.vars.inner.edges ?? []);
    }
  };
  collectEdges(nodes, graph?.edges ?? []);
  const incoming = new Set(allEdges.map((e: any) => e.to));
  const outgoing = new Set(allEdges.map((e: any) => e.from));
  // Une box (switch/loop) a une sortie si son inner a des branches qui sortent
  // vers des nœuds externes (ex. resolve_repo/condition → repo_default).
  const boxHasOut = (n: any): boolean => {
    if (!n?.vars?.inner?.nodes) return false;
    const innerIds = new Set(n.vars.inner.nodes.map((x: any) => x.id));
    return (n.vars.inner.edges ?? []).some((e: any) => innerIds.has(e.from) && !innerIds.has(e.to));
  };
  const violations: any[] = [];
  for (const n of nodes) {
    const tags = n.tags ?? [];
    const isEntry = n.type === 'entrypoint' || tags.includes('entrypoint');
    const isExit = n.type === 'exitpoint' || n.type === 'exit_error' || tags.includes('exitpoint');
    if (!isEntry && !incoming.has(n.id)) {
      violations.push({ id: n.id, kind: 'no_in' });
    }
    if (!isExit && !outgoing.has(n.id) && !boxHasOut(n)) {
      violations.push({ id: n.id, kind: 'no_out' });
    }
  }
  return violations;
}

/**
 * check_fsm — RÈGLE d'entrée/sortie sur le FSM DÉPLIÉ (top + boxes, récursif) :
 *   tout nœud qui n'est NI un entrypoint NI un exitpoint doit avoir :
 *     - ≥ 1 ENTRÉE   (les entrées `error` COMPTENT)
 *     - ≥ 1 SORTIE non-error (les sorties `error` NE COMPTENT PAS)
 * Le graphe est d'abord APLATI (unfoldAllFsm) : les switch/loop routent leurs
 * branches via leur `condition` interne, donc un nœud cible d'une branche
 * interne a bien son entrée, et une box n'est pas faussement « sans sortie ».
 * Retourne [{ id, kind: 'no_in'|'no_out', type, path }] — path = niveau box
 * d'origine (vide = top).
 */
export function check_fsm(graph: any): any[] {
  const violations: any[] = [];
  const isEntry = (n: any) => n.type === 'entrypoint' || (n.tags ?? []).includes('entrypoint');
  const isExit = (n: any) => n.type === 'exitpoint' || n.type === 'exit_error' || (n.tags ?? []).includes('exitpoint');

  // Règle STRUCTURE : une flèche entrante OU sortante d'une box ne doit pas
  // être DANS la box (inner) — elle doit être au niveau parent (arêtes de
  // sortie de la box, comme les branches d'un switch). Une arête interne dont
  // une extrémité n'est pas un nœud du niveau est une violation :
  //   - `cross_edge` : l'autre extrémité EXISTE (dans le graphe) → frontière
  //     de box mal placée.
  //   - `dangling`   : l'autre extrémité n'existe NULLE PART → référence à un
  //     step inexistant (ex. `next: end` sans step `end`).
  const allIds = new Set<string>();
  const collectIds = (nodes: any[]) => {
    for (const n of nodes) { allIds.add(n.id); if (n.vars?.inner?.nodes) collectIds(n.vars.inner.nodes); }
  };
  collectIds(graph.nodes ?? []);
  const checkCross = (nodes: any[], edges: any[], path: string[]) => {
    const levelIds = new Set(nodes.map((n: any) => n.id));
    for (const e of edges ?? []) {
      const fromIn = levelIds.has(e.from), toIn = levelIds.has(e.to);
      if (fromIn === toIn) continue;
      const ext = fromIn ? e.to : e.from;
      const p = path.join('/');
      if (allIds.has(ext)) {
        violations.push({ id: `${e.from}→${e.to}`, kind: 'cross_edge', path: p });
      } else {
        violations.push({ id: `${e.from}→${e.to}`, kind: 'dangling', missing: ext, path: p });
      }
    }
    for (const n of nodes) {
      if (n.vars?.inner?.nodes) checkCross(n.vars.inner.nodes, n.vars.inner.edges ?? [], [...path, n.id]);
    }
  };
  checkCross(graph.nodes ?? [], graph.edges ?? [], []);

  // Aplatit le graphe (feuilles + arêtes routées à travers les boxes).
  let flat: { nodes: any[]; edges: any[] };
  try {
    const u = unfoldAllFsm(graph);
    flat = { nodes: u.nodes, edges: u.edges };
  } catch {
    flat = { nodes: graph?.nodes ?? [], edges: graph?.edges ?? [] };
  }

  const ins = new Map<string, number>(), outs = new Map<string, number>();
  for (const n of flat.nodes) { ins.set(n.id, 0); outs.set(n.id, 0); }
  for (const e of flat.edges ?? []) {
    if (ins.has(e.to)) ins.set(e.to, ins.get(e.to)! + 1);              // entrées error comprises
    if (outs.has(e.from) && e.type !== 'error') outs.set(e.from, outs.get(e.from)! + 1); // sorties error exclues
  }
  for (const n of flat.nodes) {
    if (isEntry(n) || isExit(n)) continue;
    if (ins.get(n.id) === 0) violations.push({ id: n.id, kind: 'no_in', type: n.type, path: '' });
    if (outs.get(n.id) === 0) violations.push({ id: n.id, kind: 'no_out', type: n.type, path: '' });
  }
  return violations;
}

// Convertit le YAML d'un agent en GraphDoc (vue FSM) — via le modèle
// hiérarchique createFsmSubGraphFrom(agent). Chaque step/skill/loop/agent
// est dépliable ; les arêtes connectent les points atomiques, et les
// entrypoint/exitpoints sont des demi-arêtes redirigées quand on déplie.
import { createFsmSubGraphFrom, buildBodySteps, type FsmComponent } from '../theme_graphe/fsmHierarchy.ts';

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
    inputs: s.inputs,
    conditions: s.conditions,
    default: s.default,
    condition: s.condition,
    body: s.body ? { steps: (s.body.steps ?? []).map((b: any) => yamlStepToComponent(b)) } : undefined,
    next: s.next,
    on_error: s.on_error,
    tags: s.tags,
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

export function agentYamlToGraph(data: any, name: string, skillsMap: Record<string, any> = {}): any {
  if (!data?.entrypoints?.main?.steps?.length) {
    return { id: `agent-${name}`, title: name, nodes: [], edges: [] };
  }
  const agent = yamlAgentToComponent(data, name);
  const sg = createFsmSubGraphFrom(agent);
  // Inline les skills référencées (définition réelle du catalogue) :
  //  - workflow.steps → sous-graphe des steps internes de la skill
  //  - sinon (python/llm) → métadonnées (description, inputs, outputs)
  //  - implémentation python → badge "skill basique"
  enrichSkillNodes(sg.nodes, skillsMap);
  // Nœud ENTRYPOINT « main » : le point d'entrée du FSM → 1er step (pick).
  // Le 1er step top-level reçoit le tag entrypoint (déjà posé par le
  // générateur hiérarchique).
  const nodes = [...sg.nodes];
  const edges = [...sg.edges];
  nodes.unshift({
    id: 'main', type: 'entrypoint', label: 'main', ref: 'entry',
    tags: ['entrypoint'], vars: {},
  });
  const firstId = sg.entrypoint;
  if (firstId && firstId !== 'main') {
    edges.unshift({ from: 'main', to: firstId, label: 'entry', type: 'next' });
  }
  return { id: `agent-${name}`, title: name, nodes, edges };
}

/** Références de skills d'un agent : fn des steps call + section `skills:`.
 *  `git/end_exec@v1`, `workflow/it_is_done@v1`, … */
export function collectSkillRefs(data: any): string[] {
  const refs = new Set<string>();
  const walk = (steps: any[] | undefined) => {
    for (const s of steps ?? []) {
      if (typeof s?.fn === 'string' && s.fn.includes('/')) refs.add(s.fn);
      if (s?.body?.steps) walk(s.body.steps);
      if (s?.inner) walk([s.inner]);
    }
  };
  walk(data?.entrypoints?.main?.steps);
  for (const s of data?.skills ?? []) if (typeof s === 'string' && s.includes('/')) refs.add(s);
  return [...refs];
}

/** Enrichit récursivement les nœuds skill avec leur contenu de catalogue. */
function enrichSkillNodes(nodes: any[], skillsMap: Record<string, any>): void {
  for (const n of nodes ?? []) {
    if (n.type === 'skill' && n.ref && skillsMap[n.ref]) {
      const sk = skillsMap[n.ref];
      const impl = sk?.implementation;
      // Badge « skill basique » : codée directement en Python (pas de YAML steps).
      if (impl?.type === 'python' || !sk?.workflow?.steps) {
        n.tags = [...new Set([...(n.tags ?? []), 'basic_skill'])];
      }
      // Le label du nœud skill porte la description du catalogue (1 ligne).
      const desc = (sk?.description ?? '').trim();
      if (desc && !String(n.label ?? '').includes('\n')) {
        n.label = `${n.label}\n${desc.length > 90 ? desc.slice(0, 89) + '…' : desc}`;
      }
      // Le nœud interne de la skill (`git_step/skill`) devient dépliable avec
      // son contenu RÉEL : steps du workflow, sinon input → output.
      if (!n.vars?.inner && sk) {
        n.vars = { ...(n.vars ?? {}), inner: skillInnerGraph(sk, n.id) };
      }
    }
    if (n.vars?.inner?.nodes) enrichSkillNodes(n.vars.inner.nodes, skillsMap);
  }
}

/** Sous-graphe d'une skill depuis sa définition catalogue.
 *  - workflow.steps → les steps internes (comme un body d'agent)
 *  - sinon (skill atomique python) → UNIQUEMENT input (entrypoint) → output
 *    (exitpoint). Le label du nœud skill porte la description. */
function skillInnerGraph(sk: any, prefix: string): any {
  const wf = sk?.workflow;
  const steps: any[] = wf?.steps ?? [];
  if (steps.length) {
    return buildBodySteps(steps.map((s: any) => yamlStepToComponent(s)), `${prefix}/`, ['skill']);
  }
  const inputs = sk?.inputs && typeof sk?.inputs === 'object'
    ? Object.entries(sk.inputs) as [string, any][] : [];
  const outputs = sk?.outputs && typeof sk?.outputs === 'object'
    ? Object.entries(sk.outputs) as [string, any][] : [];
  const inLabel = ['input', ...inputs.map(([k, v]) => `${k}: ${v?.type ?? 'any'}${v?.required ? ' *' : ''}`)].join('\n');
  const outLabel = ['output', ...outputs.map(([k, v]) => `${k}: ${v?.type ?? 'any'}`)].join('\n');
  const inId = `${prefix}/in`;
  const outId = `${prefix}/out`;
  return {
    nodes: [
      { id: inId, type: 'skill_input', label: inLabel, ref: 'input', tags: ['entrypoint'], vars: {} },
      { id: outId, type: 'skill_output', label: outLabel, ref: 'output', tags: [], vars: {} },
    ],
    edges: [{ from: inId, to: outId, label: '', type: 'next' }],
    entrypoint: inId,
    exitpoints: [outId],
    tags: ['skill'],
  };
}

// Vue TASKFLOW : le FSM (déjà construit) AUQUEL on ajoute les TOKENS de tâches
// (task_type). Chaque skill consomme (token-in) / produit (token-out) les
// tokens, branchés sur les steps du FSM qui les utilisent.
// - token_task_pick   : token-in (le type pioché)
// - token_task_create : token-out (le type créé)
// - token_task_modify : token-in (courant) → token-out (nouveau type)
// - git/end_exec      : token-out (new_task_type, transition du pipeline)
// - token_task_release: token-out (erreur_agent)
// - exit_loop_too_hard: token-out (too_hard)
export function agentYamlToTaskflow(data: any, name: string, skillsMap: Record<string, any> = {}): any {
  const g = agentYamlToGraph(data, name, skillsMap);
  const steps: any[] = data?.entrypoints?.main?.steps ?? [];
  if (!steps.length) return g;

  const role = data?.role ?? '';
  const roleToTask: Record<string, string> = {
    codeur: 'coding', relecteur: 'code_review', test_runner: 'testing_code',
    explore: 'exploration', planificateur: 'analysis', architecte: 'analysis',
    orchestrateur: 'merger_code',
  };
  const taskRole = roleToTask[role] ?? role;
  const pipelineNext: Record<string, string> = {
    coding: 'code_review', code_review: 'merger_code', merger_code: 'testing_code',
    testing_code: 'done', analysis: 'merge_split', merge_split: 'done',
    exploration: 'done',
  };
  // Skills / bundles qui produisent un token quand appelées par le LLM.
  const skillProductions: Record<string, string> = {
    'workflow/exit_loop_too_hard@v1': 'too_hard',
    'workflow/task_verdict@v1': 'done',
    'workspace/task_done@v1': 'done',
    'workspace/token_task_release@v1': 'erreur_agent',
  };
  const bundleTokenTools: Record<string, string[]> = {
    dev: ['end_exec', 'token_task_create', 'token_task_modify',
          'token_task_release', 'task_verdict'],
    workspace_verdict: ['end_exec', 'token_task_modify', 'task_verdict', 'task_done'],
    coding_work: ['it_is_done', 'exit_loop_too_hard', 'making_progress'],
  };
  const toolProductions: Record<string, string> = {
    token_task_release: 'erreur_agent',
    exit_loop_too_hard: 'too_hard',
    task_verdict: 'done',
    task_done: 'done',
  };

  const tokenNodes = new Map<string, string>();
  const ensureToken = (tid: string, kind: 'token-in' | 'token-out') => {
    if (!tokenNodes.has(tid)) {
      tokenNodes.set(tid, kind);
      g.nodes.push({ id: tid, type: kind, label: tid, ref: tid, tags: [kind], vars: {} });
    }
  };
  const consume = (tid: string, stepId: string, label = 'pick') => {
    ensureToken(tid, 'token-in');
    g.edges.push({ from: tid, to: stepId, label, type: 'token' });
  };
  const produce = (tid: string, stepId: string, label = '→') => {
    ensureToken(tid, 'token-out');
    g.edges.push({ from: stepId, to: tid, label, type: 'success' });
  };
  const grabType = (raw: any, dflt: string): string => {
    const m = String(raw ?? '').match(/([\w]+)/);
    return m ? m[1] : dflt;
  };
  // task_types est un array YAML `[{type: coding, ...}]` → capturer le type.
  const grabTaskType = (raw: any, dflt: string): string => {
    const m = String(raw ?? '').match(/type:\s*([\w]+)/);
    return m ? m[1] : dflt;
  };

  // Parcourt les steps du FSM (top + body de boucle) — mêmes ids que le graphe.
  const walk = (stepList: any[], prefix: string) => {
    for (const s of stepList) {
      const id = `${prefix}${s.id}`;
      const fn: string = s.fn ?? '';
      if (fn.includes('token_task_pick') || fn.includes('task_claim')) {
        const tt = grabTaskType(s.inputs?.task_types, taskRole);
        consume(tt, id);
      } else if (fn.includes('token_task_create')) {
        produce(grabTaskType(s.inputs?.task?.task_type ?? s.inputs?.task_type, 'task'), id);
      } else if (fn.includes('token_task_modify')) {
        consume(taskRole, id);
        produce(grabType(s.inputs?.new_task_type, 'code_review'), id);
      } else if (fn.includes('end_exec')) {
        const nt = grabType(s.inputs?.new_task_type, pipelineNext[taskRole] ?? 'code_review');
        produce(nt, id);
      } else if (fn.includes('token_task_release')) {
        produce('erreur_agent', id);
      } else if (fn.includes('exit_loop_too_hard')) {
        produce('too_hard', id);
      }
      // LLM_CALL : les skills/bundles peuvent produire des tokens.
      if (s.type === 'llm_call') {
        for (const sk of s.skills ?? []) {
          const prod = skillProductions[sk as string];
          if (prod) produce(prod, id);
        }
        for (const b of s.bundles ?? []) {
          for (const t of (bundleTokenTools[b as string] ?? [])) {
            // end_exec / token_task_modify exposés → transition du pipeline
            // (ex. coding → code_review).
            if (t === 'end_exec' || t === 'token_task_modify') {
              const nt = pipelineNext[taskRole];
              if (nt && nt !== 'done') produce(nt, id);
            }
            const prod = toolProductions[t];
            if (prod) produce(prod, id);
          }
        }
      }
      if (s.type === 'while' && s.body?.steps) walk(s.body.steps, `${id}/body/`);
    }
  };
  walk(steps, '');

  return { ...g, id: `taskflow-${name}`, title: `${name} — taskflow` };
}

export function prefixGraphIds(g: any, prefix: string): any {
  const ren = (id: string) => `${prefix}${id}`;
  const walk = (nodes: any[] | undefined, edges: any[] | undefined) => {
    for (const n of nodes ?? []) {
      n.id = ren(n.id);
      const inner = n.vars?.inner;
      if (inner) {
        walk(inner.nodes, inner.edges);
        if (inner.entrypoint) inner.entrypoint = ren(inner.entrypoint);
        if (inner.exitpoints) inner.exitpoints = (inner.exitpoints as string[]).map(ren);
      }
    }
    for (const e of edges ?? []) {
      e.from = ren(e.from);
      e.to = ren(e.to);
    }
  };
  walk(g.nodes, g.edges);
  return g;
}


export function GrapheAgentPanel({ ctx }: { ctx: any }) {
  const { data } = usePoll<any>(
    ctx.api.post, 'catalogue/agents/list', {}, 10000,
    (res) => res?.result ?? {}, true,
  );
  const agents: any[] = data?.agents ?? [];
  const [sel, setSel] = useState<string>('');
  const [view, setView] = useState<'yaml' | 'yaml-inline' | 'fsm' | 'taskflow' | 'fsm-boxed' | 'taskflow-boxed'>('fsm');
  const [agentData, setAgentData] = useState<any>(null);
  const [agentYaml, setAgentYaml] = useState('');
  const [err, setErr] = useState<string | null>(null);
  // Définitions des skills référencées par l'agent (inline/dépliage).
  const [skillsMap, setSkillsMap] = useState<Record<string, any>>({});
  // Données de TOUS les agents (vues boxed) + skills globales.
  const [allAgentsData, setAllAgentsData] = useState<Record<string, any> | null>(null);
  const [allSkills, setAllSkills] = useState<Record<string, any>>({});

  // Chargement de l'agent : fetch une fois par sélection.
  useEffect(() => {
    if (!sel) { setAgentData(null); setAgentYaml(''); setSkillsMap({}); return; }
    setErr(null);
    ctx.api.post('catalogue/agents/get', { name: sel }).then((res: any) => {
      const r = res?.result ?? res ?? {};
      if (r.status === 'error') { setErr(r.error); setAgentData(null); return; }
      setAgentYaml(r.yaml ?? '');
      setAgentData(r.data ?? null);
    }).catch((e: any) => setErr(String(e?.message ?? e)));
  }, [sel, ctx.api.post]);

  // Chargement des skills référencées (catalogue/skills/get) pour le dépliage
  // et le badge « skill basique ». Best-effort : si une skill est introuvable
  // (codée hors catalogue), on continue sans elle.
  useEffect(() => {
    if (!agentData) return;
    let alive = true;
    const refs = collectSkillRefs(agentData);
    if (!refs.length) { setSkillsMap({}); return; }
    Promise.all(refs.map(async (name) => {
      try {
        const res = await ctx.api.post('catalogue/skills/get', { name });
        const sk = res?.result?.skill ?? res?.skill ?? res?.result;
        return sk && sk.name ? [name, sk] as const : null;
      } catch { return null; }
    })).then((rows) => {
      if (!alive) return;
      const entries = rows.filter((r): r is [string, any] => !!r);
      setSkillsMap(Object.fromEntries(entries));
    });
    return () => { alive = false; };
  }, [agentData, sel, ctx.api.post]);

  // ── Vues BOXED : charge TOUS les agents (une fois par liste stable) ──
  // Best-effort : si un get échoue (500/429/timeout), on continue avec les
  // autres. L'API est stabilisée via une ref (évite les relances si l'identité
  // de ctx.api.post change).
  const apiRef = React.useRef(ctx.api.post);
  apiRef.current = ctx.api.post;
  const agentsKey = agents.map((a: any) => a.name).join('|');
  useEffect(() => {
    if (!agents.length) return;
    let alive = true;
    // Par lots de 4 : évite la rafale de gets (rate limit 30 req/min par
    // route → un Promise.all sur 14 requêtes fait des 429). Best-effort : un
    // échec est skippé, les autres agents sont chargés.
    const map: Record<string, any> = {};
    const fetchOne = async (a: any) => {
      try {
        const res = await apiRef.current('catalogue/agents/get', { name: a.name });
        const r = res?.result ?? res ?? {};
        if (r.status === 'ok' && r.data) map[a.name] = r.data;
      } catch { /* skip */ }
    };
    const lots: any[][] = [];
    for (let i = 0; i < agents.length; i += 4) lots.push(agents.slice(i, i + 4));
    (async () => {
      for (const lot of lots) await Promise.all(lot.map(fetchOne));
      if (alive) setAllAgentsData({ ...map });
    })();
    return () => { alive = false; };
  }, [agentsKey]);

  // Skills globales (toutes les définitions du catalogue) pour les vues boxed.
  useEffect(() => {
    apiRef.current('catalogue/skills/list', {}).then((res: any) => {
      const list = res?.result?.skills ?? res?.skills ?? [];
      const map: Record<string, any> = {};
      for (const s of list ?? []) if (s?.name) map[s.name] = s;
      setAllSkills(map);
    }).catch(() => {});
  }, []);

  // Calcul en MÉMOIRE des graphes : recalculé UNIQUEMENT quand agentData ou sel
  // change (pas à chaque poll du catalogue → pas de blip/re-layout).
  // - graphFsm      : le FSM hiérarchique (steps dépliables).
  // - graphTaskflow : ÉTAPE 1 — le FSM copié et TOUT déplié (pas encore de
  //                   tokens ni de transformation Pétri).
  const { graphFsm, graphTaskflow } = useMemo(() => {
    return {
      graphFsm: agentData ? agentYamlToGraph(agentData, sel, skillsMap) : null,
      graphTaskflow: agentData ? buildTaskflowDoc(agentYamlToGraph(agentData, sel, skillsMap), sel, agentData) : null,
    };
  }, [agentData, sel, skillsMap]);

  // Graphe BOXED :
  //  - FSM boxed : chaque agent est une box contenant son FSM.
  //  - Taskflow boxed : chaque agent est une box contenant son taskflow (étape 1).
  const boxedGraph = useMemo(() => {
    if (!allAgentsData) return null;
    const boxedTaskflow = view === 'taskflow-boxed';
    const nodes = Object.keys(allAgentsData).map((name) => {
      const inner = boxedTaskflow
        ? buildTaskflowDoc(agentYamlToGraph(allAgentsData[name], name, allSkills), name, allAgentsData[name])
        : agentYamlToGraph(allAgentsData[name], name, allSkills);
      prefixGraphIds(inner, `${name}/`);
      return {
        id: name, type: 'agent', label: name, ref: 'agent',
        tags: ['entrypoint'], vars: { inner },
      };
    });
    return { id: 'agents', title: `Agents (${nodes.length})`, nodes, edges: [] };
  }, [allAgentsData, allSkills, view]);

  const shownGraph = view === 'taskflow' ? graphTaskflow : graphFsm;
  const violations = view === 'fsm' && graphFsm ? check_fsm(graphFsm) : [];

  // ── Thème graphe : depuis le store global (contrôlé par le menu
  //    « Affichage → Thème graphe »). Le store charge liste + contenu. ──
  const gTheme = useGraphThemeControl(ctx.api.post);
  useEffect(() => { gTheme.ensureLoaded(); }, [gTheme]);

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
        {/* Onglets : graphe d'agent (FSM/Taskflow) · boîtes d'agents (boxed) · YAML */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
          <button className="mw-btn" onClick={() => setView('fsm')}
            style={{ padding: '2px 10px', fontSize: 11, opacity: view === 'fsm' ? 1 : 0.5 }}>
            {ctx.t?.('panels.graphe-agent.fsm') ?? 'FSM'}
          </button>
          <button className="mw-btn" onClick={() => setView('taskflow')}
            style={{ padding: '2px 10px', fontSize: 11, opacity: view === 'taskflow' ? 1 : 0.5 }}>
            {ctx.t?.('panels.graphe-agent.taskflow') ?? 'Taskflow'}
          </button>
          <span style={{ borderLeft: '1px solid #334155', margin: '0 4px' }} />
          <button className="mw-btn" onClick={() => setView('fsm-boxed')}
            style={{ padding: '2px 10px', fontSize: 11, opacity: view === 'fsm-boxed' ? 1 : 0.5,
                     borderColor: view === 'fsm-boxed' ? '#38bdf8' : undefined,
                     color: view === 'fsm-boxed' ? '#38bdf8' : undefined }}>
            FSM boxed
          </button>
          <button className="mw-btn" onClick={() => setView('taskflow-boxed')}
            style={{ padding: '2px 10px', fontSize: 11, opacity: view === 'taskflow-boxed' ? 1 : 0.5,
                     borderColor: view === 'taskflow-boxed' ? '#38bdf8' : undefined,
                     color: view === 'taskflow-boxed' ? '#38bdf8' : undefined }}>
            Taskflow boxed
          </button>
          <span style={{ borderLeft: '1px solid #334155', margin: '0 4px' }} />
          <button className="mw-btn" onClick={() => setView('yaml')}
            style={{ padding: '2px 10px', fontSize: 11, opacity: view === 'yaml' ? 1 : 0.5 }}>
            {ctx.t?.('panels.graphe-agent.yaml') ?? 'YAML'}
          </button>
          <button className="mw-btn" onClick={() => setView('yaml-inline')}
            style={{ padding: '2px 10px', fontSize: 11, opacity: view === 'yaml-inline' ? 1 : 0.5,
                     borderColor: view === 'yaml-inline' ? '#4ade80' : undefined,
                     color: view === 'yaml-inline' ? '#4ade80' : undefined }}>
            YAML inline
          </button>
        </div>
        {err && <div style={{ color: '#f87171', marginBottom: 4 }}>{err}</div>}
        {view === 'fsm-boxed' || view === 'taskflow-boxed' ? (
          boxedGraph ? (
            <GrapheSubPanel doc={boxedGraph} theme={gTheme.obj} editable={false} engine="reactflow" height="100%" />
          ) : (
            <div style={{ color: '#475569', padding: 20, textAlign: 'center' }}>Chargement des agents…</div>
          )
        ) : !sel ? (
          <div style={{ color: '#475569', padding: 20, textAlign: 'center' }}>
            {ctx.t?.('panels.graphe-agent.selection') ?? 'Sélectionnez un agent'}
          </div>
        ) : sel && agentData ? (
          <>
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
            {view === 'yaml' || view === 'yaml-inline' ? (
              view === 'yaml-inline' ? (
                <pre style={{ flex: 1, minHeight: 0, overflow: 'auto', margin: 0, fontSize: 10,
                             background: 'rgba(22,101,52,.15)', padding: 8, borderRadius: 6,
                             border: '1px solid #4ade80', color: '#86efac' }}>
                  <span style={{ color: '#4ade80', fontWeight: 700 }}>
                    ⚡ YAML inline (défini dans le fichier de l'agent) — {agentYaml.length} chars :
                  </span>
                  {'\n' + agentYaml}
                </pre>
              ) : (
                <pre style={{ flex: 1, minHeight: 0, overflow: 'auto', margin: 0, fontSize: 10,
                             background: 'rgba(15,23,42,.5)', padding: 8, borderRadius: 6,
                             border: '1px solid var(--mw-border, #1e293b)', color: '#cbd5e1' }}>
                  {agentYaml}
                </pre>
              )
            ) : (
              <GrapheSubPanel doc={shownGraph} theme={gTheme.obj} editable={false} engine="reactflow" height="100%"
                autoExpandAll={view === 'taskflow'}
                taskflowMode={view === 'taskflow'}
                onZipAll={zip_all} onUnzipAll={unzip_all} />
            )}
          </>
        ) : (
          <div style={{ color: '#475569', padding: 20, textAlign: 'center' }}>Chargement…</div>
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
  // Insertion de menu : « Affichage → Thème graphe » (rempli dynamiquement
  // avec les thèmes graphe du daemon par App.tsx, action theme-graphe:set).
  menu: [
    { labelKey: 'menu.themeGraphe', action: 'theme-graphe:set', path: ['menu.affichage'] },
  ],
  langEmbedded: LANG_FR,
  langEmbeddedEn: LANG_EN,
  declaration: () => '[graphe-agent] Catalogue agents + FSM / Taskflow / YAML',
  component: GrapheAgentPanel,
};

export const langFr = LANG_FR;
