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
  tags?: string[];         // tags déclarés dans le YAML (llm, heavy, sleep, sandbox…)
}

/** Types de nœuds ATOMIQUES (pas de sous-graphe). */
export function isAtomicNode(node: FsmNode): boolean {
  return !node.vars?.inner;
}

/** Construit le nœud externe (pliée) d'un composant dépliable.
 *  Les tags du parent = tags propres + tags hérités du CONTENU (bottom-up) :
 *  un while contenant un llm_call obtient le tag llm ; un step call wait_for
 *  obtient sleep (déjà géré par componentTags). */
export function outerNodeOf(c: FsmComponent, extra: Partial<FsmNode> = {}, prefix = ''): FsmNode {
  const inner = buildSubGraph(c, [], prefix);
  // Tags bottom-up : on agrège les tags du inner (corps de boucle, sous-steps…).
  const innerTags = inner.tags ?? [];
  const ownTags = componentTags(c);
  const allTags = [...new Set([...ownTags, ...innerTags])];
  return {
    id: prefix + c.id,
    type: componentOuterType(c),
    label: c.label ?? c.id,
    ref: c.fn || c.type || c.kind,
    tags: allTags,
    vars: { inner },
    ...extra,
  };
}

/** Type visuel du nœud externe selon le composant. */
function componentOuterType(c: FsmComponent): string {
  if (c.kind === 'agent') return 'agent';
  if (c.kind === 'skill') return 'skill';
  if (c.kind === 'loop') return 'flow';
  if (c.kind === 'switch' || c.kind === 'if') return 'flow';
  return stepOuterType(c);
}

function stepOuterType(c: FsmComponent): string {
  if (c.type === 'end') return c.status === 'FAILED' ? 'exit_error' : 'exitpoint';
  if (c.type === 'llm_call') return 'llm';
  if (c.type === 'call') return c.fn ? 'skill' : 'fonction';
  // Steps de contrôle (set_variable, break, continue, etc.) = nœuds goto.
  if (c.type === 'set_variable' || c.type === 'break' || c.type === 'continue') return 'step';
  return c.fn ? 'fonction' : 'step';
}

/** Tags d'un composant (propres + hérités + déclarés YAML). */
function componentTags(c: FsmComponent): string[] {
  const tags: string[] = [];
  if (c.type === 'llm_call') tags.push('llm');
  if (c.fn?.includes('token_task_create')) tags.push('token_create');
  if (c.fn?.includes('token_task_pick')) tags.push('token_eat');
  if (c.fn?.includes('wait_for')) tags.push('sleep');       // wait_for = sleep (hérité par le parent)
  if (c.fn?.includes('sleep') && !c.fn?.includes('wait_for')) tags.push('sleep'); // sablier
  if (c.kind === 'loop') tags.push('loop');
  // Tags déclarés dans le YAML (ex. tags: [sandbox, heavy]) — propagés aussi.
  if (c.tags) for (const t of c.tags) if (!tags.includes(t)) tags.push(t);
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
 * SORTIES (une par condition). Le point de branchement vit au niveau PARENT :
 * les arêtes de sortie `box → cible` (conditions + default) sont émises par
 * buildBodySteps. Le inner ne garde QUE la condition (entrypoint/exitpoint) —
 * si on ajoutait aussi `condition → cible` ici, on DOUBLERAIT les transitions
 * quand la box est dépliée (routage box → exitpoint → condition).
 */
function switchSubGraph(c: FsmComponent, inherited: string[], prefix: string): FsmSubGraph {
  const tags = [...inherited, ...componentTags(c), 'condition'];
  const condId = `${prefix}${c.id}/condition`;
  const nodes: FsmNode[] = [{
    id: condId, type: 'condition', label: c.variable || c.id,
    ref: c.variable || 'switch', tags: [...tags, 'entrypoint'], vars: {},
  }];
  // L'entrée ET la sortie passent par la condition (point de branchement).
  return {
    nodes, edges: [], entrypoint: condId, exitpoints: [condId],
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
  let bodyExitpoints: string[] = [];
  if (c.body?.steps?.length) {
    const body = buildBodySteps(c.body.steps, bodyPrefix, tags);
    nodes.push(...body.nodes);
    edges.push(...body.edges);
    bodyEntry = body.entrypoint;
    bodyExitpoints = body.exitpoints;
  }
  // La condition pointe vers le 1er step du body (si true).
  if (bodyEntry) edges.push({ from: condId, to: bodyEntry, label: 'true', type: 'loop' });
  // Les SORTIES de la boucle sont SES EXITPOINTS : la condition (sortie
  // "false" → le `next` du while) + les breaks du body. Le nœud PARENT porte
  // l'arête `next → cible` (buildBodySteps), distribuée vers CHAQUE exitpoint
  // au rendu (fan-out) → break_done → after_loop, condition → after_loop, etc.
  // Pas de nœud "false" artificiel, et aucune arête interne vers l'extérieur.
  return {
    nodes, edges, entrypoint: condId,
    exitpoints: [condId, ...bodyExitpoints],
    // Tags bottom-up : on agrège les tags des nœuds du body (llm, sleep,
    // sandbox…) pour que le nœud outer du while les hérite.
    tags: [...new Set([...tags, ...nodes.flatMap((n) => n.tags ?? [])])],
  };
}

/**
 * SKILL : le call est lib.skill(vars) → entrypoint = le skill lui-même.
 * Déplié, on montre le contenu de la skill (inputs/outputs ou steps internes).
 */
function skillSubGraph(c: FsmComponent, inherited: string[], prefix: string): FsmSubGraph {
  const tags = [...inherited, ...componentTags(c), 'skill'];
  const id = `${prefix}${c.id}/skill`;
  // Le graphe représente l'AUTOMATE (la machine à états du FSM). Les inputs
  // du step call ne sont PAS des états : ils n'apparaissent pas dans le flux
  // (sinon ils n'ont pas d'entrée et cassent l'ordre).
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
export function buildBodySteps(steps: FsmComponent[], prefix: string, inherited: string[]): FsmSubGraph {
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
    // SWITCH / IF : les branches (conditions + default) SORTENT de la BOX au
    // niveau parent. « Plié → tout se réduit au nœud parent » : sans ces
    // arêtes, une box switch pliée semble sans sortie, et ses cibles externes
    // (ex. back_to_loop) semblent sans entrée (seule la branche interne
    // `condition → cible` les relie, invisible pliée). Déplié, le routage
    // redirige la box vers son entrypoint/exitpoint (la condition) et la
    // dédup (buildExpandedGraph) élimine le doublon condition→cible.
    if (s.type === 'switch' || s.type === 'if') {
      const seen = new Set<string>();
      for (const c of s.conditions ?? []) {
        const t = resolve(c.next);
        if (t && !seen.has(t)) {
          seen.add(t);
          edges.push({ from: id, to: t, label: `${s.variable ?? '?'}${c.operator ?? ''}${c.value ?? ''}`, type: 'next' });
        }
      }
      const dflt = resolve(s.default);
      if (dflt && !seen.has(dflt)) {
        edges.push({ from: id, to: dflt, label: 'else', type: 'next' });
      }
    }
    // continue → retourne à la condition de la boucle englobante.
    if (s.type === 'continue') {
      const condId = prefix.replace(/\/body\/$/, '/condition');
      edges.push({ from: id, to: condId, label: 'loop', type: 'loop' });
    }
  }
  return { nodes, edges, entrypoint, exitpoints, tags: inherited };
}

/**
 * ENTRÉE PRINCIPALE : construit le sous-graphe FSM complet d'un AGENT
 * (le graphe du panneau Graphe Agent).
 */
export function createFsmSubGraphFrom(agent: FsmComponent): FsmSubGraph {
  return buildSubGraph(agent, []);
}
