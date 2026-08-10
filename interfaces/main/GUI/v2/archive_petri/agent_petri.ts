export function agentYamlToPetri(data: any, name: string, skillsMap: Record<string, any> = {}): any {
  const g = agentYamlToGraph(data, name, skillsMap);

  // Convertit récursivement un FSM (vars.inner) en réseau de Pétri. Chaque
  // step structurel (skill/loop/switch) reste une BOX (petri-box) — la même
  // que dans le FSM — dépliée par défaut et NON repliable (frozen).
  const fsmToPetriDoc = (fsm: any): any => {
    if (!fsm?.nodes) return fsm;
    let net: PetriNet = fsmToPetri(fsm);
    net = absorbConditionOuts(net);  // les branches de switch → arêtes, pas de nœuds
    const subBoxed = new Map<string, any>();
    for (const n of fsm.nodes) {
      if (n.vars?.inner?.nodes?.length) subBoxed.set(n.id, fsmToPetriDoc(n.vars.inner));
    }
    const out = petriToGraph(net, subBoxed);
    return {
      nodes: out.nodes, edges: out.edges,
      entrypoint: fsm.entrypoint, exitpoints: fsm.exitpoints,
    };
  };

  // Sous-graphes dépliables : convertis en Pétri (récursivement).
  const boxed = new Map<string, any>();
  const collectBoxed = (nodes: any[] | undefined) => {
    for (const n of nodes ?? []) {
      if (n.vars?.inner?.nodes?.length) boxed.set(n.id, fsmToPetriDoc(n.vars.inner));
      if (n.vars?.inner?.nodes) collectBoxed(n.vars.inner.nodes);
    }
  };
  collectBoxed(g.nodes);

  let net: PetriNet = fsmToPetri(g, g.nodes[0]?.id);
  net = absorbConditionOuts(net);
  addPetriTokens(net, data);

  // Réseau NON plié (0 fold) : le folding interactif (xAy→z) est géré par le
  // GrapheSubPanel.
  const out = petriToGraph(net, boxed);
  return {
    id: `petri-${name}`, title: `${name} — petri`,
    nodes: out.nodes, edges: out.edges,
  };
}

/**
 * Ajoute les tokens comme PLACES de Pétri + les consommations/productions.
 *
 * Modèle : un JETON D'ACTIVITÉ unique navigue dans le FSM (les places) via
 * les transitions de contrôle (arêtes). Toutes les transitions sont d'ORDRE 1.
 *   - pick   (token_task_pick)   : sa transition de SORTIE consomme 1 jeton
 *                                  du type pioché (token → transition).
 *   - create / modify / end_exec / release / too_hard : sa transition de
 *     SORTIE produit 1 jeton du type (transition → token).
 *   - end   : puits 0-sortie — le jeton d'activité est détruit (fin du flux).
 */
function addPetriTokens(net: PetriNet, data: any): void {
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
  const grabType = (raw: any, dflt: string): string => {
    const m = String(raw ?? '').match(/([\w]+)/);
    return m ? m[1] : dflt;
  };
  const grabTaskType = (raw: any, dflt: string): string => {
    const m = String(raw ?? '').match(/type:\s*([\w]+)/);
    return m ? m[1] : dflt;
  };

  // Ids des places de TOKEN préfixés par `token:` — évite les collisions avec
  // les steps FSM qui portent le même nom (ex. le step `too_hard` et le token
  // `too_hard`) → pas d'auto-arc parasite.
  const tokenId = (t: string) => `token:${t}`;
  const ensurePlace = (id: string, label: string) => {
    if (!net.nodes.some((n) => n.id === id)) {
      net.nodes.push({ id, kind: 'place', label, meta: 'token' });
    }
  };
  // Transitions de SORTIE d'un step (hors arêtes d'erreur : pas de token).
  const outTransitionsOf = (stepId: string): string[] =>
    net.arcs.filter((a) => a.from === stepId)
      .map((a) => a.to)
      .filter((tid) => {
        const t = net.nodes.find((n) => n.id === tid);
        return t?.kind === 'transition' && t.meta !== 'error';
      });
  // Consommation d'ordre 1 sur les transitions de sortie : token → transition.
  const addConsume = (type: string, stepId: string) => {
    ensurePlace(tokenId(type), type);
    for (const tid of outTransitionsOf(stepId)) {
      if (!net.arcs.some((x) => x.from === tokenId(type) && x.to === tid)) {
        net.arcs.push({ from: tokenId(type), to: tid, label: 'consume' });
      }
    }
  };
  // Production d'ordre 1 sur les transitions de sortie : transition → token.
  const addProduce = (type: string, stepId: string) => {
    ensurePlace(tokenId(type), type);
    for (const tid of outTransitionsOf(stepId)) {
      if (!net.arcs.some((x) => x.from === tid && x.to === tokenId(type))) {
        net.arcs.push({ from: tid, to: tokenId(type), label: 'produce' });
      }
    }
  };
  // PUITS : transition 0-sortie — le jeton d'activité est détruit à la fin.
  const sink = (stepId: string) => {
    const sid = `t:${stepId}:sink`;
    if (net.nodes.some((n) => n.id === sid)) return;
    net.nodes.push({ id: sid, kind: 'transition', label: 'fin', meta: 'token' });
    net.arcs.push({ from: stepId, to: sid, label: '' });
  };

  const walk = (stepList: any[] | undefined, prefix: string) => {
    for (const s of stepList ?? []) {
      const id = `${prefix}${s.id}`;
      const fn: string = s.fn ?? '';
      if (fn.includes('token_task_pick') || fn.includes('task_claim')) {
        addConsume(grabTaskType(s.inputs?.task_types, taskRole), id);
      } else if (fn.includes('token_task_create')) {
        addProduce(grabTaskType(s.inputs?.task?.task_type ?? s.inputs?.task_type, 'task'), id);
      } else if (fn.includes('token_task_modify')) {
        addConsume(taskRole, id);
        addProduce(grabType(s.inputs?.new_task_type, 'code_review'), id);
      } else if (fn.includes('end_exec')) {
        addProduce(grabType(s.inputs?.new_task_type, pipelineNext[taskRole] ?? 'code_review'), id);
      } else if (fn.includes('token_task_release')) {
        addProduce('erreur_agent', id);
      } else if (fn.includes('exit_loop_too_hard')) {
        addProduce('too_hard', id);
      }
      if (s.type === 'llm_call') {
        for (const sk of s.skills ?? []) {
          if (sk === 'workflow/exit_loop_too_hard@v1') addProduce('too_hard', id);
          if (sk === 'workflow/task_verdict@v1' || sk === 'workspace/task_done@v1') addProduce('done', id);
        }
      }
      // Terminaison : le jeton d'activité est détruit (puits 0-sortie).
      if (s.type === 'end') sink(id);
      if (s.type === 'while' && s.body?.steps) walk(s.body.steps, `${id}/body/`);
    }
  };
  walk(data?.entrypoints?.main?.steps, '');
}


/** Signature token d'un agent : types consommés (pick) et produits (create/
 *  end_exec/release/too_hard…) — pour le réseau de Pétri multi-agents. */
export function agentTokenSignature(data: any): { consumes: string[]; produces: string[] } {
  const consumes = new Set<string>();
  const produces = new Set<string>();
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
  const grabType = (raw: any, dflt: string): string => {
    const m = String(raw ?? '').match(/([\w]+)/);
    return m ? m[1] : dflt;
  };
  const grabTaskType = (raw: any, dflt: string): string => {
    const m = String(raw ?? '').match(/type:\s*([\w]+)/);
    return m ? m[1] : dflt;
  };
  const walk = (stepList: any[] | undefined, prefix: string) => {
    for (const s of stepList ?? []) {
      const fn: string = s.fn ?? '';
      if (fn.includes('token_task_pick') || fn.includes('task_claim')) {
        consumes.add(grabTaskType(s.inputs?.task_types, taskRole));
      } else if (fn.includes('token_task_create')) {
        produces.add(grabTaskType(s.inputs?.task?.task_type ?? s.inputs?.task_type, 'task'));
      } else if (fn.includes('token_task_modify')) {
        consumes.add(taskRole);
        produces.add(grabType(s.inputs?.new_task_type, 'code_review'));
      } else if (fn.includes('end_exec')) {
        produces.add(grabType(s.inputs?.new_task_type, pipelineNext[taskRole] ?? 'code_review'));
      } else if (fn.includes('token_task_release')) {
        produces.add('erreur_agent');
      } else if (fn.includes('exit_loop_too_hard')) {
        produces.add('too_hard');
      }
      if (s.type === 'llm_call') {
        for (const sk of s.skills ?? []) {
          if (sk === 'workflow/exit_loop_too_hard@v1') produces.add('too_hard');
          if (sk === 'workflow/task_verdict@v1' || sk === 'workspace/task_done@v1') produces.add('done');
        }
      }
      if (s.type === 'while' && s.body?.steps) walk(s.body.steps, `${prefix}${s.id}/body/`);
    }
  };
  walk(data?.entrypoints?.main?.steps, '');
  return { consumes: [...consumes], produces: [...produces] };
}

/**
 * Réseau de Pétri MULTI-AGENTS (taskflow boxed) : la table + les types de
 * tokens sont des places GLOBALES ; chaque agent est une box (petri-box,
 * dépliable) avec une transition d'ENTRÉE (pick, par type consommé) et des
 * transitions de SORTIE (production, par type produit).
 */
export function agentsPetriGraph(
  agentsData: Record<string, any>,
  skillsMap: Record<string, any>,
): any {
  const nodes: any[] = [];
  const edges: any[] = [];
  const sigs = Object.entries(agentsData).map(([name, data]) => ({
    name, ...agentTokenSignature(data),
  }));
  const types = new Set<string>();
  for (const s of sigs) {
    for (const t of s.consumes) types.add(t);
    for (const t of s.produces) types.add(t);
  }

  // La table + les types (places globales).
  nodes.push({ id: 'task_table', type: 'place', label: 'task_table', vars: {} });
  for (const t of types) {
    nodes.push({ id: `token:${t}`, type: 'place', label: t, vars: {} });
    nodes.push({ id: `t:table->${t}`, type: 'transition', label: '→', vars: {} });
    edges.push({ from: 'task_table', to: `t:table->${t}`, type: 'next' });
    edges.push({ from: `t:table->${t}`, to: `token:${t}`, type: 'next' });
  }

  // Les agents (boxes) + leurs transitions d'entrée/sortie.
  for (const s of sigs) {
    const inner = agentYamlToPetri(agentsData[s.name], s.name, skillsMap);
    prefixGraphIds(inner, `${s.name}/`);
    nodes.push({ id: s.name, type: 'petri-box', label: s.name, ref: s.name, vars: { inner } });
    for (const t of s.consumes) {
      const tid = `t:${s.name}:pick:${t}`;
      nodes.push({ id: tid, type: 'transition', label: 'pick', vars: {} });
      edges.push({ from: `token:${t}`, to: tid, type: 'next' });
      edges.push({ from: tid, to: s.name, type: 'next' });
    }
    for (const t of s.produces) {
      const tid = `t:${s.name}:produce:${t}`;
      nodes.push({ id: tid, type: 'transition', label: '→', vars: {} });
      edges.push({ from: s.name, to: tid, type: 'next' });
      edges.push({ from: tid, to: `token:${t}`, type: 'next' });
    }
  }

  return { id: 'agents-petri', title: `Agents — petri (${sigs.length})`, nodes, edges };
}


/** Préfixe tous les ids d'un graphe (nœuds, edges, entrypoint/exitpoints des
 *  sous-graphes) — utilisé par les vues BOXED pour éviter les collisions
 *  d'ids entre les agents. */
