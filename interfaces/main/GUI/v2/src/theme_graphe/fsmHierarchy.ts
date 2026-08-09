// fsmHierarchy.ts — modèle HIÉRARCHIQUE du graphe FSM.
//
// Un nœud est soit ATOMIQUE (pas de sous-graphe), soit DÉPLIABLE (a un
// sous-graphe dans vars.inner). Chaque sous-graphe expose :
//   - nodes / edges  : le contenu interne
//   - entrypoint     : id du nœud interne où ENTRENT les arêtes du parent
//   - exitpoints     : ids des nœuds internes d'où SORTENT les arêtes
//   - tags           : tags hérités du parent (llm, heavy_tool, entrypoint…)
//
// Les entrypoint/exitpoints sont des DEMI-ARÊTES : l'arête parente est
// redirigée vers l'entrypoint interne quand le nœud est déplié, et part des
// exitpoints internes. Plié → tout se réduit au nœud parent.

export interface FsmNode {
  id: string;
  type: string;            // entrypoint | exitpoint | exit_error | llm | skill | step | switch | loop | condition | ...
  label: string;
  ref?: string;
  tags?: string[];
  vars?: Record<string, any>;  // inner = { nodes, edges, entrypoint, exitpoints, tags }
}

export interface FsmEdge {
  from: string;
  to: string;
  label?: string;
  type?: string;
}

export interface FsmSubGraph {
  nodes: FsmNode[];
  edges: FsmEdge[];
  entrypoint: string | null;
  exitpoints: string[];
  tags: string[];
}

/** Composant à déplier : un step FSM (avec sa structure interne). */
export interface FsmComponent {
  kind: 'step' | 'switch' | 'if' | 'loop' | 'skill' | 'agent';
  id: string;
  label?: string;
  fn?: string;
  type?: string;           // type de step (call, llm_call, set_variable, end, break, continue…)
  status?: string;
  inputs?: Record<string, any>;
  // switch / if
  variable?: string;
  conditions?: { operator?: string; value?: any; next?: string }[];
  default?: string;
  // while / for
  condition?: any;
  body?: { steps: FsmComponent[] };
  next?: string;
  on_error?: string;
  // skills / agents internes
  inner?: FsmComponent;    // skill référencée ou agent référencé
}

/** Types de nœuds ATOMIQUES (pas de sous-graphe). */
export function isAtomicNode(node: FsmNode): boolean {
  return !node.vars?.inner;
}

/** Construit le nœud externe (pliée) d'un composant dépliable. */
export function outerNodeOf(c: FsmComponent, extra: Partial<FsmNode> = {}, prefix = ''): FsmNode {
  return {
    id: prefix + c.id,
    type: componentOuterType(c),
    label: c.label ?? c.id,
    ref: c.fn || c.type || c.kind,
    tags: componentTags(c),
    vars: { inner: buildSubGraph(c, [], prefix) },
    ...extra,
  };
}

/** Type visuel du nœud externe selon le composant. */
function componentOuterType(c: FsmComponent): string {
  if (c.kind === 'agent') return 'agent';
  if (c.kind === 'skill') return 'skill';
  if (c.kind === 'loop') return 'loop';
  if (c.kind === 'switch' || c.kind === 'if') return 'switch';
  return stepOuterType(c);
}

function stepOuterType(c: FsmComponent): string {
  if (c.type === 'end') return c.status === 'FAILED' ? 'exit_error' : 'exitpoint';
  if (c.type === 'llm_call') return 'llm';
  if (c.type === 'set_variable') return 'step';
  if (c.type === 'break' || c.type === 'continue') return 'step';
  if (c.type === 'call') return 'skill';
  return 'step';
}

/** Tags d'un composant (propres + hérités). */
function componentTags(c: FsmComponent): string[] {
  const tags: string[] = [];
  if (c.type === 'llm_call') tags.push('llm');
  if (c.fn?.includes('token_task_create')) tags.push('token_create');
  if (c.fn?.includes('token_task_pick')) tags.push('token_eat');
  if (c.kind === 'loop') tags.push('loop');
  return tags;
}

/**
 * Construit le sous-graphe dépliable d'un composant.
 * Dispatch par `kind` : chaque composant a sa définition (switch → condition,
 * loop → condition+body, skill/agent → contenu interne).
 * `prefix` : préfixe du nœud (ex. "A/body/") pour résoudre les cibles next.
 */
export function buildSubGraph(c: FsmComponent, inherited: string[] = [], prefix = ''): FsmSubGraph {
  switch (c.kind) {
    case 'switch':
    case 'if':    return switchSubGraph(c, inherited, prefix);
    case 'loop':  return loopSubGraph(c, inherited, prefix);
    case 'skill': return skillSubGraph(c, inherited, prefix);
    case 'agent': return agentSubGraph(c, inherited, prefix);
    default:      return stepSubGraph(c, inherited, prefix);
  }
}

/**
 * STEP : contenu d'une étape simple.
 * - call avec `inner` (skill) → le skill est le sous-graphe.
 * - sinon → atomique (pas de contenu dépliable).
 */
function stepSubGraph(c: FsmComponent, inherited: string[], prefix: string): FsmSubGraph {
  if (c.fn && c.inner) {
    return buildSubGraph(c.inner, [...inherited, ...componentTags(c)], prefix);
  }
  return { nodes: [], edges: [], entrypoint: null, exitpoints: [], tags: inherited };
}

/**
 * SWITCH / IF : un nœud `condition` avec une ENTRÉE (la variable) et N
 * SORTIES (une par condition, label = la condition). L'arête externe entre
 * dans la condition ; chaque sortie de condition pointe vers le step cible.
 */
function switchSubGraph(c: FsmComponent, inherited: string[], prefix: string): FsmSubGraph {
  const tags = [...inherited, ...componentTags(c), 'condition'];
  const condId = `${prefix}${c.id}/condition`;
  const nodes: FsmNode[] = [{
    id: condId, type: 'condition', label: c.variable || c.id,
    ref: c.variable || 'switch', tags: [...tags, 'entrypoint'], vars: {},
  }];
  const edges: FsmEdge[] = [];
  const condVar = c.variable || '?';
  const resolve = (ref?: string): string | null => ref ? `${prefix}${ref}` : null;

  for (let i = 0; i < (c.conditions ?? []).length; i++) {
    const cond = c.conditions![i];
    const exitId = `${condId}/out${i}`;
    nodes.push({ id: exitId, type: 'condition_out', label: `${condVar}=${cond.value ?? ''}`, tags: [...tags], vars: {} });
    edges.push({ from: condId, to: exitId, label: '', type: 'next' });
    const target = resolve(cond.next);
    if (target) edges.push({ from: exitId, to: target, label: String(cond.value ?? ''), type: 'next' });
  }
  if (c.default) {
    const exitId = `${condId}/else`;
    const target = resolve(c.default);
    nodes.push({ id: exitId, type: 'condition_out', label: 'else', tags: [...tags], vars: {} });
    edges.push({ from: condId, to: exitId, label: '', type: 'next' });
    if (target) edges.push({ from: exitId, to: target, label: 'else', type: 'next' });
  }
  return {
    nodes, edges, entrypoint: condId,
    exitpoints: nodes.filter((n) => n.type === 'condition_out').map((n) => n.id),
    tags,
  };
}

/**
 * LOOP (while/for) : un nœud `condition` (entrant ET sortant) qui pointe vers
 * le 1er step du body. Le body est un sous-graphe boxable/foldable.
 *  - break   = exitpoint de la boucle
 *  - continue = retourne à la condition
 *  - la fin du body revient à la condition (itération suivante) OU sort si
 *    la condition est fausse.
 */
function loopSubGraph(c: FsmComponent, inherited: string[], prefix: string): FsmSubGraph {
  const tags = [...inherited, ...componentTags(c), 'loop'];
  const condId = `${prefix}${c.id}/condition`;
  const nodes: FsmNode[] = [{
    id: condId, type: 'condition', label: `${c.kind} ${c.id}`,
    ref: c.condition ? String(c.condition) : c.kind,
    tags: [...tags, 'entrypoint', 'exitpoint'],  // condition = entrée ET sortie
    vars: {},
  }];
  const edges: FsmEdge[] = [];

  // Body : sous-graphe boxable/foldable, préfixé par la boucle.
  const bodyPrefix = `${prefix}${c.id}/body/`;
  let bodyEntry: string | null = null;
  if (c.body?.steps?.length) {
    const body = buildBodySteps(c.body.steps, bodyPrefix, tags);
    nodes.push(...body.nodes);
    edges.push(...body.edges);
    bodyEntry = body.entrypoint;
  }
  // La condition pointe vers le 1er step du body (si true).
  if (bodyEntry) edges.push({ from: condId, to: bodyEntry, label: 'true', type: 'loop' });
  // Sortie "false" de la condition → next du while (résolu au préfixe parent).
  const condExitId = `${condId}/exit`;
  nodes.push({ id: condExitId, type: 'condition_out', label: 'false', tags: [...tags, 'exitpoint'], vars: {} });
  edges.push({ from: condId, to: condExitId, label: 'false', type: 'loop' });
  const nextResolved = c.next ? `${parentOf(prefix)}${c.next}` : null;
  if (nextResolved) edges.push({ from: condExitId, to: nextResolved, label: '', type: 'next' });

  return {
    nodes, edges, entrypoint: condId,
    exitpoints: [condExitId, ...nodes.filter((n) => n.type === 'exitpoint').map((n) => n.id)],
    tags,
  };
}

/**
 * SKILL : le call est lib.skill(vars) → entrypoint = le skill lui-même.
 * Déplié, on montre le contenu de la skill (inputs/outputs ou steps internes).
 */
function skillSubGraph(c: FsmComponent, inherited: string[], prefix: string): FsmSubGraph {
  const tags = [...inherited, ...componentTags(c), 'skill'];
  const id = `${prefix}${c.id}/skill`;
  const nodes: FsmNode[] = [{
    id, type: 'skill', label: c.fn || c.id, ref: c.fn || c.id,
    tags: [...tags, 'entrypoint'], vars: {},
  }];
  return { nodes, edges: [], entrypoint: id, exitpoints: [id], tags };
}

/**
 * AGENT : son FSM (main). Déplié → les steps top-level du body, avec
 * entrypoint = 1er step, exitpoints = les steps end/break.
 */
function agentSubGraph(c: FsmComponent, inherited: string[], prefix: string): FsmSubGraph {
  const tags = [...inherited, ...componentTags(c), 'agent'];
  if (c.body?.steps?.length) {
    return buildBodySteps(c.body.steps, prefix, tags);
  }
  const id = `${prefix}${c.id}/entry`;
  const nodes: FsmNode[] = [{
    id, type: 'entrypoint', label: c.label ?? c.id,
    ref: 'entry', tags: [...tags, 'entrypoint'], vars: {},
  }];
  return { nodes, edges: [], entrypoint: id, exitpoints: [id], tags };
}

/** Construit les steps d'un body (sous-graphe boxable/foldable). Chaque step
 * reçoit un préfixe (`prefix`) ; les références next/on_error/conditions sont
 * résolues relativement à ce préfixe. */
function buildBodySteps(steps: FsmComponent[], prefix: string, inherited: string[]): FsmSubGraph {
  const nodes: FsmNode[] = [];
  const edges: FsmEdge[] = [];
  let entrypoint: string | null = null;
  const exitpoints: string[] = [];

  const resolve = (ref?: string): string | null =>
    ref ? `${prefix}${ref}` : null;

  for (const s of steps) {
    const id = `${prefix}${s.id}`;
    // Nœud externe (dépliable) avec préfixe.
    const outer = outerNodeOf(s, {}, prefix);
    outer.id = id;
    outer.label = s.label ?? s.id;
    outer.tags = [...new Set([...inherited, ...(outer.tags ?? [])])];
    // Le 1er step du body (agent) est l'ENTRYPOINT : tag hérité (le type
    // garde son rôle visuel, ex. skill/loop).
    if (entrypoint === null && prefix === '') {
      outer.tags = [...new Set([...(outer.tags ?? []), 'entrypoint'])];
    }
    nodes.push(outer);
    if (!entrypoint) entrypoint = id;
    if (s.type === 'end' || s.type === 'break') exitpoints.push(id);

    // next / on_error (résolus au préfixe).
    const nxt = resolve(s.next);
    if (nxt) edges.push({ from: id, to: nxt, label: 'next', type: 'next' });
    const oe = resolve(s.on_error);
    if (oe) edges.push({ from: id, to: oe, label: 'err', type: 'error' });
    // switch/if : conditions + default (résolus au préfixe).
    if (s.kind === 'switch' || s.kind === 'if') {
      for (const c of s.conditions ?? []) {
        const t = resolve(c.next);
        if (t) edges.push({ from: id, to: t, label: String(c.value ?? ''), type: 'next' });
      }
      const d = resolve(s.default);
      if (d) edges.push({ from: id, to: d, label: 'else', type: 'next' });
    }
    // continue → retourne à la condition de la boucle englobante.
    if (s.type === 'continue') {
      const condId = prefix.replace(/\/body\/$/, '/condition');
      edges.push({ from: id, to: condId, label: 'loop', type: 'loop' });
    }
  }
  return { nodes, edges, entrypoint, exitpoints, tags: inherited };
}

/** Parent (préfixe) d'un id de sous-nœud, ex. "A/body/x" → "A". */
function parentOf(id: string): string {
  const i = id.lastIndexOf('/');
  return i > 0 ? id.slice(0, i) : '';
}

/**
 * ENTRÉE PRINCIPALE : construit le sous-graphe FSM complet d'un AGENT
 * (le graphe du panneau Graphe Agent).
 */
export function createFsmSubGraphFrom(agent: FsmComponent): FsmSubGraph {
  return buildSubGraph(agent, []);
}
