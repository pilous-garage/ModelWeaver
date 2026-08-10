// petriFold.ts — moteur de réduction de Réseaux de Pétri (folding/boxing).
//
// Le task flow d'un agent est vu comme un réseau de Pétri :
//   - TRANSITIONS : les steps du FSM (pick, git_step, end_exec…) — une action.
//   - PLACES      : les états entre steps (arêtes) ET les tokens de tâches
//                   (coding, code_review…) qui transitent.
//   - ARCS        : place → transition (consommation) / transition → place
//                   (production).
//
// Règles de réduction (style Berthelot, préservant le flux entrant/sortant) :
//   1. SÉQUENCE LINÉAIRE : place P avec UNE transition entrante T1 et UNE
//      sortante T2 (aucun autre flux) → fusionner T1→P→T2 en une
//      macro-transition TX (box dépliable).
//   2. BOUCLE / SCC : composante fortement connexe avec entrée/sortie uniques
//      → boxée en une macro-transition TX (le cycle reste interne).
//
// La sortie est un graphe HIÉRARCHIQUE (nœuds avec vars.inner) compatible avec
// l'infra de dépliage existante (graphExpand).

// ── Modèle ──────────────────────────────────────────────────────────

export interface PetriNode {
  id: string;
  kind: 'place' | 'transition';
  label: string;
  ref?: string;
  meta?: string;            // type de step (skill/switch/…) ou 'token'/'boxed'
  box?: string;             // appartenance (step structurel) pour les zones
  inner?: PetriNet;         // macro-transition dépliée
}

export interface PetriArc {
  from: string;             // place id
  to: string;               // transition id
  label?: string;
}

export interface PetriNet {
  nodes: PetriNode[];
  arcs: PetriArc[];
}

// ── Aides ───────────────────────────────────────────────────────────

export function makeNet(nodes: PetriNode[], arcs: PetriArc[]): PetriNet {
  return { nodes, arcs };
}

export function cloneNet(net: PetriNet): PetriNet {
  return {
    nodes: net.nodes.map((n) => ({ ...n, inner: n.inner ? cloneNet(n.inner) : undefined })),
    arcs: net.arcs.map((a) => ({ ...a })),
  };
}

/** Places d'entrée d'une transition (pré) : places ayant un arc → transition. */
export function prePlaces(net: PetriNet, tId: string): string[] {
  return net.arcs.filter((a) => a.to === tId).map((a) => a.from);
}

/** Places de sortie d'une transition (post) : places cibles d'un arc ← transition. */
export function postPlaces(net: PetriNet, tId: string): string[] {
  return net.arcs.filter((a) => a.from === tId).map((a) => a.to);
}

/** Transitions entrantes d'une place. */
export function inTransitions(net: PetriNet, pId: string): string[] {
  return net.arcs.filter((a) => a.to === pId).map((a) => a.from);
}

/** Transitions sortantes d'une place. */
export function outTransitions(net: PetriNet, pId: string): string[] {
  return net.arcs.filter((a) => a.from === pId).map((a) => a.to);
}

// ── Conversion FSM → Pétri ──────────────────────────────────────────

/**
 * Convertit un graphe FSM en réseau de Pétri :
 *   - chaque NŒUD FSM = une PLACE (un état où transitent les tâches)
 *   - chaque ARÊTE FSM = une TRANSITION (le passage d'un état à l'autre)
 * Les tokens (tâches colorées) sont ajoutés séparément (places + table).
 */
export function fsmToPetri(graph: { nodes: any[]; edges: any[] }, _entryId?: string): PetriNet {
  const nodes: PetriNode[] = [];
  const arcs: PetriArc[] = [];

  // Places = tous les nœuds FSM (main, pick, git_step, …).
  for (const n of graph.nodes) {
    nodes.push({
      id: n.id,
      kind: 'place',
      label: String(n.label ?? n.id),
      ref: n.ref,
      meta: n.type ?? 'step',
      box: n.vars?.box,   // appartenance (step structurel) pour les zones
    });
  }

  // Transitions = toutes les arêtes FSM (pré = place from, post = place to).
  // meta = le type d'arête (next/error/…) pour distinguer les sorties.
  for (const e of graph.edges ?? []) {
    const tid = `t:${e.from}->${e.to}`;
    nodes.push({ id: tid, kind: 'transition', label: e.label || '', meta: e.type || 'flow' });
    arcs.push({ from: e.from, to: tid, label: e.label });
    arcs.push({ from: tid, to: e.to, label: e.label });
  }

  return { nodes, arcs };
}

/** Attache le sous-réseau (déplié) d'une transition boxable. */
export function setInner(net: PetriNet, tId: string, inner: PetriNet): void {
  const t = net.nodes.find((n) => n.id === tId);
  if (t && t.kind === 'transition') t.inner = inner;
}

// ── Réduction : SÉQUENCE LINÉAIRE ───────────────────────────────────

/** Distance BFS de chaque place depuis l'entrée (place sans pré-transition).
 *  Le folding AUTO se fait de l'ENTRÉE vers la SORTIE. */
export function placeOrderByEntry(net: PetriNet): Map<string, number> {
  const dist = new Map<string, number>();
  const queue: string[] = [];
  for (const n of net.nodes) {
    if (n.kind === 'place' && inTransitions(net, n.id).length === 0) {
      dist.set(n.id, 0);
      queue.push(n.id);
    }
  }
  let head = 0;
  while (head < queue.length) {
    const cur = queue[head++];
    const d = dist.get(cur)!;
    for (const t of outTransitions(net, cur)) {
      for (const p of postPlaces(net, t)) {
        if (!dist.has(p)) { dist.set(p, d + 1); queue.push(p); }
      }
    }
  }
  return dist;
}

/**
 * Fusionne place P (UNE transition entrante T1, UNE sortante T2, aucun autre
 * flux) en une macro-transition TX contenant T1→P→T2. Le pré/post de TX =
 * pré(T1) ∪ post(T2) privé de P. Les fusions se font de l'ENTRÉE vers la
 * SORTIE (distance BFS). Répète jusqu'à fixpoint.
 */
export function reduceLinear(net: PetriNet): PetriNet {
  let changed = true;
  let seq = 0;
  while (changed) {
    changed = false;
    const order = placeOrderByEntry(net);
    const places = net.nodes
      .filter((n) => n.kind === 'place')
      .sort((a, b) => (order.get(a.id) ?? Infinity) - (order.get(b.id) ?? Infinity));
    for (const p of places) {
      // Place « structurelle » (flux de contrôle) : pas un token, pas la source.
      if (p.meta === 'token' || p.meta === 'entry') continue;
      const ins = inTransitions(net, p.id);
      const outs = outTransitions(net, p.id);
      if (ins.length !== 1 || outs.length !== 1) continue;
      const t1 = net.nodes.find((n) => n.id === ins[0]);
      const t2 = net.nodes.find((n) => n.id === outs[0]);
      if (!t1 || !t2) continue;
      // Les transitions de TOKEN (consommation/production) sont des points
      // structurants (entrées/sorties de l'agent) : on ne les fusionne pas.
      if (t1.meta === 'token' || t2.meta === 'token') continue;
      // Ni une transition qui touche une place de TOKEN (consomme/produit) —
      // les skills de gestion de token restent visibles avec leur transition.
      const touchesToken = (t: PetriNode): boolean =>
        net.arcs.some((a) => {
          if (a.from !== t.id && a.to !== t.id) return false;
          const other = a.from === t.id ? a.to : a.from;
          return net.nodes.find((n) => n.id === other)?.meta === 'token';
        });
      if (touchesToken(t1) || touchesToken(t2)) continue;

      // Macro-transition TX : on APLATIT les inner de T1 et T2 (s'ils sont
      // eux-mêmes boxés) pour obtenir une boîte plate pick→…→end, pas imbriquée.
      const txId = `x:${seq++}`;
      const txLabel = [t1.label, t2.label].filter(Boolean).join(' → ');
      const t1Parts = t1.inner && t1.inner.nodes.length ? t1.inner : { nodes: [t1], arcs: [] };
      const t2Parts = t2.inner && t2.inner.nodes.length ? t2.inner : { nodes: [t2], arcs: [] };
      const innerNodes = [...t1Parts.nodes, p, ...t2Parts.nodes];
      const innerArcs: PetriArc[] = [
        ...t1Parts.arcs,
        { from: t1Parts.nodes[t1Parts.nodes.length - 1].id, to: p.id },
        { from: p.id, to: t2Parts.nodes[0].id },
        ...t2Parts.arcs,
      ];
      const inner: PetriNet = { nodes: innerNodes, arcs: innerArcs };
      const tx: PetriNode = {
        id: txId, kind: 'transition', label: txLabel || txId,
        meta: 'boxed', inner,
      };
      // Remplacer : tout arc →T1 devient →TX ; tout arc T2→ devient TX→.
      const newArcs: PetriArc[] = [];
      for (const a of net.arcs) {
        if (a.to === t1.id) newArcs.push({ ...a, to: txId });
        else if (a.from === t2.id) newArcs.push({ ...a, from: txId });
        else if ((a.from === t1.id && a.to === p.id) || (a.from === p.id && a.to === t2.id)) {
          /* internalisé */
        } else newArcs.push({ ...a });
      }
      const newNodes = net.nodes.filter((n) => n.id !== t1.id && n.id !== t2.id && n.id !== p.id);
      newNodes.push(tx);
      net = { nodes: newNodes, arcs: newArcs };
      changed = true;
      break; // re-scan (les ids ont changé)
    }
  }
  return net;
}

// ── Réduction : BOUCLE / SCC (Tarjan) ───────────────────────────────

/** SCC d'un graphe dirigé (places + transitions), ordre topologique. */
export function stronglyConnectedComponents(net: PetriNet): string[][] {
  const idx = new Map<string, number>();
  const low = new Map<string, number>();
  const stack: string[] = [];
  const onStack = new Set<string>();
  const comps: string[][] = [];
  let counter = 0;

  const dfs = (v: string) => {
    idx.set(v, counter); low.set(v, counter); counter++;
    stack.push(v); onStack.add(v);
    const succ = net.arcs.filter((a) => a.from === v).map((a) => a.to);
    for (const w of succ) {
      if (!idx.has(w)) { dfs(w); low.set(v, Math.min(low.get(v)!, low.get(w)!)); }
      else if (onStack.has(w)) low.set(v, Math.min(low.get(v)!, idx.get(w)!));
    }
    if (low.get(v) === idx.get(v)) {
      const comp: string[] = [];
      let w: string | undefined;
      do { w = stack.pop(); onStack.delete(w!); comp.push(w!); } while (w !== v);
      comps.push(comp);
    }
  };
  for (const n of net.nodes) if (!idx.has(n.id)) dfs(n.id);
  return comps;
}

/**
 * Boxe chaque SCC (taille > 1, ou auto-arc) en une macro-transition. Le cycle
 * reste INTERNE (boucle conservée) ; le flux entrant/sortant est préservé.
 */
export function reduceCycles(net: PetriNet): PetriNet {
  const comps = stronglyConnectedComponents(net);
  const cyc = comps.filter((c) => c.length > 1 || hasSelfArc(net, c[0]));
  if (!cyc.length) return net;

  const inComp = new Set<string>(cyc.flat());
  const keepNodes = net.nodes.filter((n) => !inComp.has(n.id));

  // Macro-transition TX par SCC.
  let k = 0;
  for (const comp of cyc) {
    const compSet = new Set(comp);
    const entries = prePlacesAll(net, compSet);
    const exits = postPlacesAll(net, compSet);
    const label = `⟳ ${comp.length} nœuds`;
    const txId = `c:loop${k++}`;
    const inner: PetriNet = {
      nodes: net.nodes.filter((n) => compSet.has(n.id)),
      arcs: net.arcs.filter((a) => compSet.has(a.from) && compSet.has(a.to)),
    };
    // Arcs externes : le SCC reçoit depuis les places d'entrée et produit vers
    // les places de sortie (hors SCC).
    const tx: PetriNode = {
      id: txId, kind: 'transition', label, meta: 'boxed', inner,
    };
    keepNodes.push(tx);
    // Remplace les arcs : place_ext → (n'importe quel nœud du SCC) devient
    // place_ext → TX ; (n'importe quel nœud du SCC) → place_ext devient TX → place_ext.
    const newArcs: PetriArc[] = [];
    for (const a of net.arcs) {
      if (compSet.has(a.from) && compSet.has(a.to)) continue; // interne
      if (compSet.has(a.from)) newArcs.push({ ...a, from: txId });       // sortie
      else if (compSet.has(a.to)) newArcs.push({ ...a, to: txId });      // entrée
      else newArcs.push({ ...a });
    }
    net = { nodes: keepNodes, arcs: newArcs };
  }
  return net;
}

function hasSelfArc(net: PetriNet, id: string): boolean {
  return net.arcs.some((a) => a.from === id && a.to === id);
}

function prePlacesAll(net: PetriNet, comp: Set<string>): string[] {
  return net.arcs.filter((a) => comp.has(a.to) && !comp.has(a.from)).map((a) => a.from);
}
function postPlacesAll(net: PetriNet, comp: Set<string>): string[] {
  return net.arcs.filter((a) => comp.has(a.from) && !comp.has(a.to)).map((a) => a.to);
}

/** Pipeline complet : cycles puis séquences linéaires (fixpoint). */
export function fold(net: PetriNet): PetriNet {
  let n = reduceCycles(net);
  n = reduceLinear(n);
  return n;
}

// ── Sortie HIÉRARCHIQUE (compatible graphExpand) ────────────────────

/**
 * Convertit le réseau de Pétri (réduit) en GraphDoc hiérarchique :
 * place → nœud `place` (ou `petri-box` si step boxable), transition → nœud
 * `transition`, macro-transition (folding) → `petri-box` (vars.inner = sous-
 * réseau converti). `boxed` : map id → vars.inner des steps FSM dépliables
 * (skills/loops) à conserver tels quels.
 */
export function petriToGraph(net: PetriNet, boxed?: Map<string, any>): { nodes: any[]; edges: any[] } {
  const convert = (n: PetriNet): { nodes: any[]; edges: any[] } => {
    const subNodes: any[] = [];
    const subEdges: any[] = [];
    for (const p of n.nodes) {
      if (p.kind === 'place') {
        if (boxed?.has(p.id)) {
          subNodes.push({
            id: p.id, type: 'petri-box', label: p.label, ref: p.ref,
            vars: { inner: boxed.get(p.id) },
          });
        } else {
          // `vars.token` marque les places de TOKEN (non foldables, non places
          // de contrôle) ; `vars.box` = appartenance (zone).
          subNodes.push({
            id: p.id, type: 'place', label: p.label || '·', ref: p.ref,
            vars: {
              ...(p.meta === 'token' ? { token: true } : {}),
              ...(p.box ? { box: p.box } : {}),
            },
          });
        }
      } else if (p.inner && p.inner.nodes.length) {
        const inner = convert(p.inner);
        subNodes.push({
          id: p.id, type: 'petri-box', label: p.label, ref: p.ref,
          vars: { inner: { nodes: inner.nodes, edges: inner.edges, entrypoint: p.inner.nodes[0]?.id, exitpoints: [] } },
        });
      } else {
        subNodes.push({ id: p.id, type: 'transition', label: p.label, ref: p.ref, vars: {} });
      }
    }
    for (const a of n.arcs) {
      // Pas de label sur les arcs (le Pétri est un graphe de flux, muet).
      subEdges.push({ from: a.from, to: a.to, type: 'next' });
    }
    return { nodes: subNodes, edges: subEdges };
  };

  const top = convert(net);
  return { nodes: top.nodes, edges: top.edges };
}

// ── Folding INTERACTIF : liste de folds à règles ────────────────────
// Chaque mouvement de folding/unfolding met à jour une LISTE de folds
// (foldState, ordre d'insertion). Chaque fold appartient à une RÈGLE :
//   - 'seq' : séquentielle xAy → z (place A à 1 entrée/1 sortie).
//   - 'par' : parallèle xB + yB → zB (2 places convergentes fusionnées).
// D'autres règles (boucle, skill, agent…) seront ajoutées à la même liste.

export interface Fold {
  zId: string;
  rule: 'seq' | 'par';
  label: string;
  center?: string;       // seq : la place A (bouton − sous la place)
  bornes: string[];      // par : les transitions du groupe (bouton − dessous)
  removed: string[];     // ids retirés de la surface (absorbés dans z)
  rerouteIn: string[];   // arcs →ces ids deviennent → z
  rerouteOut: string[];  // arcs depuis ces ids deviennent depuis z
  addNodes?: any[];      // nœuds à ajouter
  addEdges?: { from: string; to: string; type?: string }[];
  inner: { nodes: any[]; edges: any[]; entrypoint?: string; exitpoints?: string[] };
  path?: string[];       // chemin des nodes (vars.inner) pour atteindre le
                         // sous-graphe ; vide = surface
}

/** Nœud de contrôle : place OU petri-box (step dépliable), non token. */
const isControl = (n: any): boolean => !!n && n.type !== 'transition' && !n.vars?.token;

/**
 * Absorbe les nœuds `condition_out` (branches de switch en sortie d'une
 * condition) : ce sont des ARTEFACTS — ils deviennent des ARÊTES (transitions
 * labelées) directes `condition → cible`. La transition de sortie (qui porte
 * le label de la branche) est conservée ; on y rebranche l'entrée de la
 * condition. Applique récursivement l'absorption (jusqu'à fixpoint).
 */
export function absorbConditionOuts(net: PetriNet): PetriNet {
  let changed = true;
  let guard = 0;
  while (changed && guard++ < 500) {
    changed = false;
    for (const p of net.nodes) {
      if (p.kind !== 'place' || p.meta !== 'condition_out') continue;
      const ins = inTransitions(net, p.id);
      const outs = outTransitions(net, p.id);
      if (ins.length !== 1 || outs.length !== 1) continue;
      const tIn = ins[0];
      const tOut = outs[0];
      // Rebranche les entrées de tIn → tOut ; retire tIn et la place condition_out.
      const newArcs: PetriArc[] = [];
      for (const a of net.arcs) {
        if (a.from === tIn || a.to === tIn) {
          if (a.to === tIn) newArcs.push({ ...a, to: tOut });
          continue; // sortie de tIn retirée
        }
        if (a.from === p.id || a.to === p.id) continue; // arcs de la place retirés
        newArcs.push({ ...a });
      }
      net = { nodes: net.nodes.filter((n) => n.id !== tIn && n.id !== p.id), arcs: newArcs };
      changed = true;
      break;
    }
  }
  return net;
}
/** Nœud de contrôle (défini plus haut). */

/**
 * Déplie récursivement le FSM (unfold ALL) : chaque step boxable (skill/loop/
 * switch) est remplacé par son sous-graphe. Les arêtes sont routées vers les
 * entrypoints/exitpoints internes. Retourne un graphe PLAT + la map des
 * exitpoints par step (pour brancher les tokens de consommation/production).
 */
export function flattenFsm(g: { nodes: any[]; edges: any[] }): {
  nodes: any[]; edges: any[]; exitOf: Map<string, string>;
} {
  const nodeById = new Map<string, any>();
  const collect = (n: any) => {
    nodeById.set(n.id, n);
    for (const s of n.vars?.inner?.nodes ?? []) collect(s);
  };
  g.nodes.forEach(collect);

  const resolve = (id: string, isExit: boolean): string => {
    let cur = id;
    for (let i = 0; i < 12; i++) {
      const n = nodeById.get(cur);
      if (!n?.vars?.inner?.nodes?.length) return cur;
      const inner = n.vars.inner;
      const next = isExit ? inner.exitpoints?.[0] : inner.entrypoint;
      cur = next ?? inner.nodes?.[0]?.id ?? cur;
    }
    return cur;
  };

  const exitOf = new Map<string, string>();
  const collectExit = (list: any[]) => {
    for (const n of list) {
      if (n.vars?.inner?.nodes?.length) {
        exitOf.set(n.id, resolve(n.id, true));
        collectExit(n.vars.inner.nodes);
      }
    }
  };
  collectExit(g.nodes);

  const nodes: any[] = [];
  const edges: any[] = [];
  // Chaque nœud garde son appartenance (`vars.box` = step structurel parent)
  // pour afficher les BOXES comme zones, tout en restant dans le graphe plat.
  const walkNodes = (list: any[], boxId?: string) => {
    for (const n of list) {
      if (n.vars?.inner?.nodes?.length) {
        walkNodes(n.vars.inner.nodes, n.id);
      } else {
        nodes.push({ ...n, vars: { ...(n.vars ?? {}), box: boxId } });
      }
    }
  };
  walkNodes(g.nodes);

  const pushEdges = (list: any[]) => {
    for (const e of list) {
      edges.push({ from: resolve(e.from, true), to: resolve(e.to, false), label: e.label, type: e.type });
    }
  };
  pushEdges(g.edges);
  const walkEdges = (list: any[]) => {
    for (const n of list) {
      if (n.vars?.inner?.nodes?.length) {
        pushEdges(n.vars.inner.edges ?? []);
        walkEdges(n.vars.inner.nodes);
      }
    }
  };
  walkEdges(g.nodes);
  return { nodes, edges, exitOf };
}

/**
 * Règle SÉQUENTIELLE : xAy → z, où x = transition ENTRANTE, A = place,
 * y = transition SORTANTE. x, A et y sont ABSORBÉS dans z (z = [x, A, y]).
 * Les arêtes vers x → vers z ; les arêtes depuis y → depuis z.
 */
export function findSeqFolds(doc: { nodes: any[]; edges: any[] }): Fold[] {
  const byId = new Map<string, any>(doc.nodes.map((n) => [n.id, n]));
  const results: Fold[] = [];
  for (const A of doc.nodes) {
    // A = place simple OU petri-box (skill/loop) à 1 entrée/1 sortie — les
    // skills comme token_task_pick, git_checkout, check_done sont foldables.
    if ((A.type !== 'place' && A.type !== 'petri-box') || A.vars?.token || A.vars?.folded) continue;
    const inEdges = doc.edges.filter((e) => e.to === A.id);
    const outEdges = doc.edges.filter((e) => e.from === A.id);
    if (inEdges.length !== 1 || outEdges.length !== 1) continue;
    const tIn = byId.get(inEdges[0].from);
    const tOut = byId.get(outEdges[0].to);
    if (tIn?.type !== 'transition' || tOut?.type !== 'transition') continue;
    // tIn a exactement 1 sortie (vers A) ; tOut exactement 1 entrée (depuis A).
    const tInOut = doc.edges.filter((e) => e.from === tIn.id);
    const tOutIn = doc.edges.filter((e) => e.to === tOut.id);
    if (tInOut.length !== 1 || tOutIn.length !== 1) continue;
    if (tInOut[0].to !== A.id || tOutIn[0].from !== A.id) continue;
    const ids = [tIn.id, A.id, tOut.id];
    results.push({
      zId: `z:${A.id}`, rule: 'seq', label: A.label ?? A.id,
      center: A.id,                 // bouton − SOUS la place A
      bornes: [tIn.id, tOut.id],
      removed: ids,
      rerouteIn: [tIn.id],          // arcs →x → z
      rerouteOut: [tOut.id],        // arcs depuis y → depuis z
      inner: {
        nodes: doc.nodes.filter((n) => ids.includes(n.id)),
        edges: [
          { from: tIn.id, to: A.id, type: 'next' },
          { from: A.id, to: tOut.id, type: 'next' },
        ],
        entrypoint: tIn.id, exitpoints: [tOut.id],
      },
    });
  }
  return results;
}

/**
 * Règle PARALLÈLE : groupe de transitions avec MÊMES entrées ET MÊMES sorties
 * → une unique transition z. La sauvegarde (par_folded) = le groupe.
 */
export function findParFolds(doc: { nodes: any[]; edges: any[] }): Fold[] {
  const groups = new Map<string, { trs: string[]; ins: string[]; outs: string[] }>();
  for (const t of doc.nodes) {
    if (t.type !== 'transition' || t.vars?.folded) continue;
    const ins = doc.edges.filter((e) => e.to === t.id).map((e) => e.from);
    const outs = doc.edges.filter((e) => e.from === t.id).map((e) => e.to);
    const key = [...ins].sort().join(',') + '|' + [...outs].sort().join(',');
    if (!groups.has(key)) groups.set(key, { trs: [], ins, outs });
    groups.get(key)!.trs.push(t.id);
  }
  const results: Fold[] = [];
  for (const { trs, ins, outs } of groups.values()) {
    if (trs.length < 2) continue;
    // Le groupe est plié en z ; le inner montre les transitions parallèles +
    // leurs places d'entrée/sortie.
    const placeIds = [...ins, ...outs];
    const zId = `z:${trs.join('+')}`;
    const firstLabel = (doc.nodes.find((n) => n.id === trs[0]) as any)?.label ?? trs[0];
    results.push({
      zId, rule: 'par',
      label: firstLabel,      // z s'appelle x (une des transitions)
      bornes: trs,            // bouton − sous chaque transition du groupe
      removed: trs,
      rerouteIn: trs,         // arcs → tr_i → → z
      rerouteOut: trs,        // arcs depuis tr_i → depuis z
      inner: {
        nodes: doc.nodes.filter((n) => trs.includes(n.id) || placeIds.includes(n.id)),
        edges: doc.edges.filter((e) =>
          (trs.includes(e.from) && placeIds.includes(e.to)) ||
          (trs.includes(e.to) && placeIds.includes(e.from))),
        entrypoint: ins[0], exitpoints: outs,
      },
    });
  }
  return results;
}

/** Règles de folding applicables, RÉCURSIVEMENT dans les sous-graphes
 *  (vars.inner) : les éléments des boxes dépliées sont foldables. Chaque fold
 *  reçoit son chemin + un zId unique. Les BOXES elles-mêmes (petri-box) ne
 *  sont pas pliables (findSeqFolds exige des places simples). */
export function findFolds(doc: { nodes: any[]; edges: any[] }): Fold[] {
  const out: Fold[] = [];
  const walk = (d: { nodes: any[]; edges: any[] }, path: string[]) => {
    for (const f of [...findSeqFolds(d), ...findParFolds(d)]) {
      out.push(path.length
        ? { ...f, path, zId: `${path.join('/')}/${f.zId}` }
        : f);
    }
    for (const n of d.nodes) {
      // Ne descend pas dans le contenu d'un fold déjà plié (sinon re-pliage
      // infini) ; les petri-box (skills/loops) restent traversés.
      if (n.vars?.inner?.nodes && !n.vars?.folded) walk(n.vars.inner, [...path, n.id]);
    }
  };
  walk(doc, []);
  return out;
}

/** Alias (compatibilité) : findFolds est déjà récursif. */
export function findFoldsRec(doc: { nodes: any[]; edges: any[] }): Fold[] {
  return findFolds(doc);
}

/** Applique un fold : rebranche les arcs entrants/sortants des bornes sur z,
 *  ET rebranche aussi les arêtes des SOUS-GRAPHES (inner des petri-box) qui
 *  référencent les nœuds absorbés (ex. le switch pointe encore vers repo_task
 *  après le fold de repo_task → re-pointé vers z). */
export function applyFoldOnce(doc: { nodes: any[]; edges: any[] }, fold: Fold): any {
  const { zId, rule } = fold;
  const mapId = (id: string) => (fold.removed.includes(id) ? zId : id);
  const rebranchGraph = (d: any): any => ({
    ...d,
    nodes: (d.nodes ?? []).map((n: any) =>
      n.vars?.inner?.nodes ? { ...n, vars: { ...n.vars, inner: rebranchGraph(n.vars.inner) } } : n),
    edges: (d.edges ?? []).map((e: any) => ({ ...e, from: mapId(e.from), to: mapId(e.to) })),
  });
  const keep = doc.nodes.filter((n) => !fold.removed.includes(n.id));
  const z: any = {
    id: zId, type: 'transition',
    label: fold.label,
    vars: { folded: true, inner: fold.inner },
  };
  const edges: any[] = [];
  for (const e of doc.edges) {
    if (fold.removed.includes(e.from) || fold.removed.includes(e.to)) {
      if (fold.rerouteIn.includes(e.to)) edges.push({ ...e, to: zId });
      else if (fold.rerouteOut.includes(e.from)) edges.push({ ...e, from: zId });
      continue; // arcs internes (dans le inner)
    }
    edges.push({ ...e });
  }
  for (const ae of fold.addEdges ?? []) edges.push({ ...ae });
  // Déduplication (le groupe parallèle rebranche plusieurs fois les mêmes
  // in/out vers z).
  const uniq = new Map<string, any>();
  for (const e of edges) uniq.set(`${e.from}->${e.to}`, e);
  const nodes = [
    ...keep.map((n: any) =>
      n.vars?.inner?.nodes ? { ...n, vars: { ...n.vars, inner: rebranchGraph(n.vars.inner) } } : n),
    z, ...(fold.addNodes ?? []),
  ];
  return { ...doc, nodes, edges: [...uniq.values()] };
}

/** Applique les folds enregistrés (ordre d'insertion), récursivement via le
 *  chemin (vars.inner) de chaque fold. */
export function applyFolds(
  doc: { nodes: any[]; edges: any[] },
  foldState: Map<string, Fold>,
): any {
  let d = doc;
  for (const fold of foldState.values()) {
    d = applyFoldPath(d, fold.path ?? [], fold);
  }
  return d;
}

function applyFoldPath(doc: { nodes: any[]; edges: any[] }, path: string[], fold: Fold): any {
  if (!path.length) return applyFoldOnce(doc, fold);
  const [head, ...rest] = path;
  return {
    ...doc,
    nodes: doc.nodes.map((n) =>
      n.id === head
        ? { ...n, vars: { ...(n.vars ?? {}), inner: applyFoldPath(n.vars?.inner ?? { nodes: [], edges: [] }, rest, fold) } }
        : n),
  };
}

/** Distance BFS d'un nœud depuis l'entrée (place sans arête entrante). */
export function nodeOrderByEntry(doc: { nodes: any[]; edges: any[] }): Map<string, number> {
  const dist = new Map<string, number>();
  const queue: string[] = [];
  for (const n of doc.nodes) {
    if (n.type === 'place' && !n.vars?.token && !doc.edges.some((e) => e.to === n.id)) {
      dist.set(n.id, 0);
      queue.push(n.id);
    }
  }
  const succ = new Map<string, string[]>();
  for (const e of doc.edges) {
    if (!succ.has(e.from)) succ.set(e.from, []);
    succ.get(e.from)!.push(e.to);
  }
  let head = 0;
  while (head < queue.length) {
    const cur = queue[head++];
    const d = dist.get(cur)!;
    for (const nx of succ.get(cur) ?? []) {
      if (!dist.has(nx)) { dist.set(nx, d + 1); queue.push(nx); }
    }
  }
  return dist;
}

/**
 * Folding AUTO récursif : d'abord le premier fold SÉQUENTIEL (surface ou
 * sous-graphe), puis quand il n'y en a plus, le premier PARALLÈLE — chaque
 * fold mettant à jour les foldables (re-détection à chaque itération).
 * fold_all et open_all opèrent à TOUS les niveaux.
 */
export function autoFold(
  doc: { nodes: any[]; edges: any[] },
): { doc: any; foldState: Map<string, Fold> } {
  const foldState = new Map<string, Fold>();
  let d = doc;
  let guard = 0;
  while (guard++ < 2000) {
    const all = findFoldsRec(d);
    if (!all.length) break;
    const seq = all.find((f) => f.rule === 'seq');
    const target = seq ?? all.find((f) => f.rule === 'par');
    if (!target) break;
    d = applyFoldPath(d, target.path ?? [], target);
    foldState.set(target.zId, target);
  }
  return { doc: d, foldState };
}
