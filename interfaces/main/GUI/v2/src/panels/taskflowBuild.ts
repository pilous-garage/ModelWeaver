// taskflowBuild.ts — construction du taskflow, ÉTAPE PAR ÉTAPE.
//
// Étape 1 : copier le FSM et tout DÉPLIER.
//   - copie (ne mute pas l'original)
//   - chaque step structurel (vars.inner non vide) est remplacé par son
//     sous-graphe déplié (récursivement) ; les arêtes sont routées vers les
//     entrypoints/exitpoints internes.
//   - retourne un graphe PLAT + le signal `fullyUnfolded: true` : on ne
//     continue la construction (transformation en Pétri, tokens…) que lorsque
//     ce signal est présent.

export interface UnfoldedFsm {
  nodes: any[];
  edges: any[];
  fullyUnfolded: true;   // signal : tout est déplié
}

/** Étape 1 — copie le FSM et le déplie TOUT. */
export function unfoldAllFsm(fsm: { nodes: any[]; edges: any[] }): UnfoldedFsm {
  // Copie profonde (on ne touche pas à l'original).
  const copy = JSON.parse(JSON.stringify(fsm));

  // Index de tous les nœuds (surface + sous-graphes) pour le routage récursif.
  const byId = new Map<string, any>();
  const collect = (n: any) => {
    byId.set(n.id, n);
    for (const s of n.vars?.inner?.nodes ?? []) collect(s);
  };
  copy.nodes.forEach(collect);

  // Routage : descend dans les containers jusqu'au premier nœud réel.
  //   entrée  → entrypoint interne ; sortie → CHAQUE exitpoint interne
  //   (fan-out : les breaks/end d'une boucle sont des sorties distinctes).
  const fanOut = (id: string, isExit: boolean, depth = 0): string[] => {
    const n = byId.get(id);
    if (!n?.vars?.inner?.nodes?.length || depth > 12) return [id];
    const inner = n.vars.inner;
    const next = isExit ? (inner.exitpoints ?? []) : [inner.entrypoint];
    const ids = next.length ? next : [inner.nodes?.[0]?.id];
    return ids.filter(Boolean).flatMap((t: string) => fanOut(t, isExit, depth + 1));
  };

  const nodes: any[] = [];
  const edges: any[] = [];

  // Nœuds : seules les FEUILLES restent (les containers sont dépliés).
  const walkNodes = (list: any[]) => {
    for (const n of list) {
      if (n.vars?.inner?.nodes?.length) walkNodes(n.vars.inner.nodes);
      else nodes.push(n);
    }
  };
  walkNodes(copy.nodes);

  // Arêtes : celles de la surface + celles des sous-graphes, routées (fan-out).
  const pushEdges = (list: any[]) => {
    for (const e of list) {
      const froms = fanOut(e.from, true);
      const tos = fanOut(e.to, false);
      for (const f of froms) {
        for (const t of tos) {
          edges.push({ from: f, to: t, label: e.label, type: e.type });
        }
      }
    }
  };
  pushEdges(copy.edges);
  const walkEdges = (list: any[]) => {
    for (const n of list) {
      if (n.vars?.inner?.nodes?.length) {
        pushEdges(n.vars.inner.edges ?? []);
        walkEdges(n.vars.inner.nodes);
      }
    }
  };
  walkEdges(copy.nodes);

  return { nodes, edges, fullyUnfolded: true };
}

/** Étape 3 — transformation : chaque arête `from → to` devient
 *  `from → TRANSITION → to`. En place, récursivement dans les boxes
 *  (vars.inner). Le nœud transition porte le label/type de l'arête. */
export function insertTransitions(g: { nodes: any[]; edges: any[] }): void {
  let counter = 0;
  const process = (nodes: any[], edges: any[]) => {
    const newNodes: any[] = [];
    const newEdges: any[] = [];
    for (const e of edges) {
      const tid = `t:${e.from}->${e.to}:${counter++}`;
      newNodes.push({
        id: tid, type: 'transition', label: e.label ?? '',
        ref: e.type ?? 'next', tags: [], vars: {},
      });
      newEdges.push({ from: e.from, to: tid, label: e.label, type: e.type });
      newEdges.push({ from: tid, to: e.to, label: e.label, type: e.type });
    }
    nodes.push(...newNodes);
    for (const n of nodes) {
      if (n.vars?.inner?.nodes) process(n.vars.inner.nodes, n.vars.inner.edges);
    }
    edges.length = 0;
    edges.push(...newEdges);
  };
  process(g.nodes, g.edges);
}

/** Construction du taskflow courant :
 *  1) COPIE EXACTE du FSM (y compris les boxes, repliables comme dans le FSM).
 *  2) TRANSFORMATION : chaque arête devient arête → transition → arête
 *     (récursivement, en place).
 *  3) TOUS les nœuds sont marqués `visible=true`.
 *  4) TOKENS : places de token + arêtes ajoutées sur la transition de sortie
 *     des steps token (consume/produce/modify_and_replace/replace). */
export function buildTaskflowDoc(
  fsm: { nodes: any[]; edges: any[] },
  name: string,
  data?: any,
): any {
  const copy = JSON.parse(JSON.stringify(fsm));
  insertTransitions(copy);
  markAllVisible(copy);
  if (data) addTokenPlaces(copy, data);
  return {
    id: `taskflow-${name}`,
    title: `${name} — taskflow`,
    nodes: copy.nodes,
    edges: copy.edges,
    meta: { copied: true, transformed: true, visible: true, tokens: !!data },
  };
}

/** Marque `vars.visible=true` sur TOUS les nœuds (récursif, y compris les
 *  boxes/inner et les transitions). */
export function markAllVisible(g: { nodes: any[] }): void {
  const walk = (nodes: any[] | undefined) => {
    for (const n of nodes ?? []) {
      n.vars = { ...(n.vars ?? {}), visible: true };
      if (n.vars.inner?.nodes) walk(n.vars.inner.nodes);
    }
  };
  walk(g.nodes);
}

const isTokenNode = (n: any) =>
  !!(n?.vars?.token || n?.vars?.tokenOp) || n?.type === 'token-in' || n?.type === 'token-out';

/**
 * check_petri — vérifie la CONNECTIVITÉ du graphe Pétri du taskflow (déplié) :
 *   chaque PLACE et chaque TRANSITION qui n'est NI un entrypoint NI un
 *   exitpoint NI un token (token_task) doit avoir :
 *     - ≥ 1 ENTRÉE
 *     - ≥ 1 SORTIE
 * Le graphe est d'abord APLATI (unfoldAllFsm) : les switch/loop routent leurs
 * branches via leur box, et les transitions dupliquées sont évitées (une seule
 * arête box → cible, les arêtes internes de switch supprimées).
 * Retourne [{ id, kind: 'no_in'|'no_out', type }].
 */
export function check_petri(g: { nodes: any[]; edges: any[] }): any[] {
  const violations: any[] = [];

  // Règle STRUCTURE : une flèche entrante/sortante d'une box ne doit pas être
  // DANS la box (même règle que check_fsm : cross_edge / dangling).
  const allIds = new Set<string>();
  const collectIds = (nodes: any[]) => {
    for (const n of nodes) { allIds.add(n.id); if (n.vars?.inner?.nodes) collectIds(n.vars.inner.nodes); }
  };
  collectIds(g.nodes ?? []);
  const checkCross = (nodes: any[], edges: any[], path: string[]) => {
    const levelIds = new Set(nodes.map((n: any) => n.id));
    for (const e of edges ?? []) {
      const fromIn = levelIds.has(e.from), toIn = levelIds.has(e.to);
      if (fromIn === toIn) continue;
      const ext = fromIn ? e.to : e.from;
      const p = path.join('/');
      if (allIds.has(ext)) violations.push({ id: `${e.from}→${e.to}`, kind: 'cross_edge', path: p });
      else violations.push({ id: `${e.from}→${e.to}`, kind: 'dangling', missing: ext, path: p });
    }
    for (const n of nodes) {
      if (n.vars?.inner?.nodes) checkCross(n.vars.inner.nodes, n.vars.inner.edges ?? [], [...path, n.id]);
    }
  };
  checkCross(g.nodes ?? [], g.edges ?? [], []);

  let flat: { nodes: any[]; edges: any[] };
  try {
    const u = unfoldAllFsm(g);
    flat = { nodes: u.nodes, edges: u.edges };
  } catch {
    flat = { nodes: g.nodes ?? [], edges: g.edges ?? [] };
  }
  const isEntry = (n: any) => n.type === 'entrypoint' || (n.tags ?? []).includes('entrypoint');
  const isExit = (n: any) => n.type === 'exitpoint' || n.type === 'exit_error' || (n.tags ?? []).includes('exitpoint');

  const ins = new Map<string, number>(), outs = new Map<string, number>();
  for (const n of flat.nodes) { ins.set(n.id, 0); outs.set(n.id, 0); }
  for (const e of flat.edges ?? []) {
    if (ins.has(e.to)) ins.set(e.to, ins.get(e.to)! + 1);
    if (outs.has(e.from)) outs.set(e.from, outs.get(e.from)! + 1);
  }
  for (const n of flat.nodes) {
    if (isEntry(n) || isExit(n) || isTokenNode(n)) continue;
    if (ins.get(n.id) === 0) violations.push({ id: n.id, kind: 'no_in', type: n.type });
    if (outs.get(n.id) === 0) violations.push({ id: n.id, kind: 'no_out', type: n.type });
  }
  return violations;
}

/**
 * check_balance — invariant global de connectivité, par niveau (top + boxes) :
 *   nb_arête == total_nb_in == total_nb_out.
 * Chaque arête contribue 1 entrée (vers sa cible) et 1 sortie (depuis sa
 * source) ; si un nœud est absent d'un niveau ou si une arête pointe vers un
 * nœud inexistant, la balance casse. Retourne [{ id, kind, ... }].
 */
export function check_balance(g: { nodes: any[]; edges: any[] }): any[] {
  const violations: any[] = [];
  const walk = (nodes: any[], edges: any[], path: string[]) => {
    const E = edges?.length ?? 0;
    const byId = new Set(nodes.map((n: any) => n.id));
    let sumIn = 0, sumOut = 0;
    for (const e of edges ?? []) {
      if (byId.has(e.to)) sumIn++; else violations.push({ id: `${e.from}→${e.to}`, kind: 'dangling_to', path: path.join('/') });
      if (byId.has(e.from)) sumOut++; else violations.push({ id: `${e.from}→${e.to}`, kind: 'dangling_from', path: path.join('/') });
    }
    if (E !== sumIn || E !== sumOut) {
      violations.push({ id: path.join('/') || 'top', kind: 'imbalance', edges: E, in: sumIn, out: sumOut });
    }
    for (const n of nodes) {
      if (n.vars?.inner?.nodes) walk(n.vars.inner.nodes, n.vars.inner.edges ?? [], [...path, n.id]);
    }
  };
  walk(g.nodes ?? [], g.edges ?? [], []);
  return violations;
}

const ROLE_TO_TASK: Record<string, string> = {
  codeur: 'coding', relecteur: 'code_review', test_runner: 'testing_code',
  explore: 'exploration', planificateur: 'analysis', architecte: 'analysis',
  orchestrateur: 'merger_code',
};
const PIPELINE_NEXT: Record<string, string> = {
  coding: 'code_review', code_review: 'merger_code', merger_code: 'testing_code',
  testing_code: 'done', analysis: 'merge_split', merge_split: 'done',
  exploration: 'done',
};

/**
 * Parcourt les nœuds du réseau et, pour les steps TOKEN
 * (token_task_pick→consume, token_task_create/end_exec→produce,
 * token_task_modify→modify_and_replace, token_task_release→replace),
 * marque `vars.visible=true` et ajoute la PLACE de token (+ arête) sur leur
 * transition normale de sortie.
 */
export function addTokenPlaces(g: { nodes: any[]; edges: any[] }, data: any): void {
  const role = data?.role ?? '';
  const taskRole = ROLE_TO_TASK[role] ?? role;
  const grabType = (raw: any, dflt: string): string => {
    const m = String(raw ?? '').match(/([\w]+)/);
    return m ? m[1] : dflt;
  };
  const grabTaskType = (raw: any, dflt: string): string => {
    const m = String(raw ?? '').match(/type:\s*([\w]+)/);
    return m ? m[1] : dflt;
  };

  const ensurePlace = (id: string, label: string) => {
    if (!g.nodes.some((n) => n.id === id)) {
      g.nodes.push({ id, type: 'place', label, ref: 'token', tags: ['token'], vars: { token: true, visible: true } });
    }
  };

  // Trouve la TRANSITION de sortie d'un step (dans le top ou un inner).
  const findOutTransition = (nodes: any[], edges: any[], id: string): { edges: any[]; trans: string } | null => {
    const out = edges.filter((e) => e.from === id)
      .map((e) => e.to)
      .find((t) => nodes.find((n) => n.id === t)?.type === 'transition');
    if (out) return { edges, trans: out };
    for (const n of nodes) {
      if (n.vars?.inner?.nodes) {
        const r = findOutTransition(n.vars.inner.nodes, n.vars.inner.edges, id);
        if (r) return r;
      }
    }
    return null;
  };

  const walkSteps = (steps: any[] | undefined, prefix: string) => {
    for (const s of steps ?? []) {
      const id = `${prefix}${s.id}`;
      const fn: string = s.fn ?? '';
      let consumeType: string | null = null;
      let produceType: string | null = null;
      let op: string | null = null;
      if (fn.includes('token_task_pick') || fn.includes('task_claim')) {
        consumeType = grabTaskType(s.inputs?.task_types, taskRole);
        op = 'consume';
      } else if (fn.includes('token_task_create')) {
        produceType = grabTaskType(s.inputs?.task?.task_type ?? s.inputs?.task_type, 'task');
        op = 'produce';
      } else if (fn.includes('token_task_modify')) {
        consumeType = taskRole;
        produceType = grabType(s.inputs?.new_task_type, 'code_review');
        op = 'modify_and_replace';
      } else if (fn.includes('token_task_release')) {
        produceType = 'erreur_agent';
        op = 'replace';
      } else if (fn.includes('end_exec')) {
        produceType = grabType(s.inputs?.new_task_type, PIPELINE_NEXT[taskRole] ?? 'code_review');
        op = 'produce';
      } else if (fn.includes('exit_loop_too_hard')) {
        produceType = 'too_hard';
        op = 'produce';
      }
      if (consumeType || produceType) {
        // Marquer le nœud token (top ou inner) : il ne peut être ni clippé ni
        // rendu invisible (les tokens relient les agents d'un swarm).
        const markToken = (nodes: any[] | undefined): boolean => {
          for (const n of nodes ?? []) {
            if (n.id === id) { n.vars = { ...(n.vars ?? {}), visible: true, tokenOp: op ?? 'token' }; return true; }
            if (n.vars?.inner?.nodes && markToken(n.vars.inner.nodes)) return true;
          }
          return false;
        };
        markToken(g.nodes);
        // Ajouter la place de token + l'arête sur la transition de sortie.
        const r = findOutTransition(g.nodes, g.edges, id);
        if (r) {
          if (consumeType) {
            ensurePlace(`token:${consumeType}`, consumeType);
            r.edges.push({ from: `token:${consumeType}`, to: r.trans, label: 'consume', type: 'token' });
          }
          if (produceType) {
            ensurePlace(`token:${produceType}`, produceType);
            r.edges.push({ from: r.trans, to: `token:${produceType}`, label: 'produce', type: 'token' });
          }
        }
      }
      if (s.type === 'while' && s.body?.steps) walkSteps(s.body.steps, `${id}/body/`);
    }
  };
  walkSteps(data?.entrypoints?.main?.steps, '');
}
