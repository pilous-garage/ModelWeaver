// taskflowClip.ts — tables + algorithmes de CLIPPAGE (zip/unzip) du taskflow.
//
// ARCHITECTURE : ÉTAT MAINTENU PAR NŒUD (jamais de rescan des arêtes).
// Chaque nœud porte dans `vars` :
//   - in_group  : ids VISIBLES U tels que U → N
//   - out_group : ids VISIBLES V tels que N → V
//   - zippable  : 'none' | 'seq' | 'par' | 'single' (le check incrémental)
//   - innerPlacesVisible : (boxes) nb de places internes VISIBLES — le check
//     SINGLE est O(1) (LAZY) : jamais de rescan de l'inner.
//   - box       : (nœuds internes) id du box parent (notifie le compteur lazy)
//
// Règles :
//   - seq    : nœud non-transition avec 1 transition entrante + 1 sortante
//              (visibles), et ces 2 transitions ont exactement ce nœud en
//              sortie/entrée → tr_1 → P → tr_2 = tr_3 (bouton sur P).
//   - par    : transitions partageant les MÊMES entrées ET sorties (visibles)
//              → 1 transition (bouton sur chaque transition du groupe).
//   - single : box dont le inner a EXACTEMENT 1 place interne visible (les
//              transitions ne comptent pas) → on rend cette place invisible et
//              on crée un nœud à sa position.
//
// AFFICHAGE (règle show) : une box est affichée ssi innerPlacesVisible >= 1
// (0 place visible → masquée, mais vars.visible reste true).
//
// MISE À JOUR LOCALES (séquentielles, jamais en parallèle) : à chaque
// add/remove dans un in_group/out_group, on RECHECK le nœud affecté via
// recheckNode (lazy, O(deg)). zip/unzip maintiennent l'état incrémentalement.
//
// `path` : chemins des boxes pour atteindre le niveau (inner) où le clip
// opère ; vide = top.

export type Zippable = 'none' | 'seq' | 'par' | 'single';
export type ClipType = 'seq' | 'par' | 'single';

export interface Clip {
  type: ClipType;
  newId: string;       // nœud créé (visible)
  zipped: string[];    // nœuds rendus invisibles (restaurés à l'unzip)
  on: string[];        // nœuds portant le bouton − (le déclencheur du zip)
  label?: string;
  path?: string[];     // chemin des boxes vers le niveau (vide = top)
}

export interface ClipTable {
  clips: Clip[];
}

export function createClipTable(): ClipTable {
  return { clips: [] };
}

// ── Helpers ─────────────────────────────────────────────────────────

export function nodeById(g: { nodes: any[] }, id: string): any {
  return g.nodes.find((n) => n.id === id);
}
export const isVisible = (n: any) => n?.vars?.visible !== false;
/** POT à token (place vars.token) : ni clippable ni masquable. Les steps token
 *  (vars.tokenOp, ex. pick) sont des steps NORMALES — foldables. */
const isTokenPot = (n: any) => !!(n?.vars?.token);
const hasInner = (n: any) => !!n?.vars?.inner?.nodes?.length;
const gkey = (a?: string[], b?: string[]) =>
  `${[...(a ?? [])].sort().join(',')}|${[...(b ?? [])].sort().join(',')}`;

/** in/out VISIBLES d'un nœud, re-dérivés depuis les arêtes (unzip). */
function groupsFromEdges(ctx: any, id: string): { ins: string[]; outs: string[] } {
  const vis = (ids: string[]) => ids.filter((u) => isVisible(nodeById(ctx, u)));
  return {
    ins: vis((ctx.edges ?? []).filter((e: any) => e.to === id).map((e: any) => e.from)),
    outs: vis((ctx.edges ?? []).filter((e: any) => e.from === id).map((e: any) => e.to)),
  };
}

// ── État : reconstruction (bulk) + recheck (incrémental, lazy) ──────

function walkLevels(g: any, path: string[], fn: (ctx: any, path: string[]) => void): void {
  fn(g, path);
  for (const n of g.nodes) {
    if (n.vars?.inner?.nodes) walkLevels(n.vars.inner, [...path, n.id], fn);
  }
}

/** Index global id → nœud (tous niveaux) : le routing rend les voisins d'un
 *  nœud référençables à travers les frontières de box. PAR GRAPHE (stocké sur
 *  le graphe racine) — évite la contamination entre plusieurs graphes. */
let _byId = new Map<string, any>();
function setIndex(g: any, map: Map<string, any>): void {
  _byId = map;
  (g as any).__mwById = map;
}
function indexGraph(nodes: any[]): void {
  for (const n of nodes ?? []) {
    _byId.set(n.id, n);
    if (n.vars?.inner?.nodes) indexGraph(n.vars.inner.nodes);
  }
}

/** Reconstruit TOUT l'état (groupes, compteur single, zippable) pour tous les
 *  niveaux. Idempotent. Appelé au chargement / si l'état est absent.
 *
 *  MODÈLE « box transparentes » : on parcourt TOUTES les arêtes et on remplit
 *  in.group_out / out.group_in, mais SEULEMENT si les deux extrémités sont des
 *  places/transitions. Les BOX ne portent JAMAIS de in/out : leurs arêtes
 *  externes sont ROUTÉES vers l'entrypoint (entrée) / les exitpoints (sortie)
 *  de leur inner (récursif), pour que les nœuds input/output des contrats
 *  aient les bons in/out (et soient foldables). */
export function rebuildState(g: any): void {
  setIndex(g, new Map());
  indexGraph(g.nodes);
  // 1) Groupes de base (box transparentes) + compteurs d'arêtes + single.
  walkLevels(g, [], (ctx) => {
    const ins = new Map<string, string[]>(), outs = new Map<string, string[]>();
    const eIn = new Map<string, number>(), eOut = new Map<string, number>();
    for (const n of ctx.nodes) { ins.set(n.id, []); outs.set(n.id, []); eIn.set(n.id, 0); eOut.set(n.id, 0); }
    for (const e of ctx.edges ?? []) {
      const a = nodeById(ctx, e.from), b = nodeById(ctx, e.to);
      if (!a || !b || !isVisible(a) || !isVisible(b)) continue;
      eOut.set(e.from, eOut.get(e.from)! + 1);
      eIn.set(e.to, eIn.get(e.to)! + 1);
      if (hasInner(a) || hasInner(b)) continue;          // les box sont ignorées
      outs.get(e.from)!.push(e.to);
      ins.get(e.to)!.push(e.from);
    }
    for (const n of ctx.nodes) {
      n.vars = {
        ...(n.vars ?? {}),
        in_group: ins.get(n.id), out_group: outs.get(n.id),
        in_edges: eIn.get(n.id), out_edges: eOut.get(n.id),
      };
    }
    for (const n of ctx.nodes) {
      if (n.vars?.inner?.nodes) {
        const ipv = n.vars.inner.nodes.filter((x: any) => x.type !== 'transition' && isVisible(x)).length;
        const ivn = n.vars.inner.nodes.filter((x: any) => isVisible(x)).length;
        n.vars = { ...(n.vars ?? {}), innerPlacesVisible: ipv, innerVisibleNodes: ivn };
        for (const s of n.vars.inner.nodes) s.vars = { ...(s.vars ?? {}), box: n.id };
      }
    }
  });
  // 2) ROUTING : une arête vers une box entre par son entrypoint (place), une
  //    arête depuis une box sort par ses exitpoints (récursif via les boxes).
  /** Entrypoint LEAF le plus profond d'un niveau (à travers les boxes). */
  const deepestIn = (level: any): string => {
    const ep = level?.entrypoint;
    if (!ep) return ep;
    const epNode = _byId.get(ep);
    if (epNode && hasInner(epNode) && epNode.vars?.inner?.entrypoint) return deepestIn(epNode.vars.inner);
    return ep;
  };
  /** Exitpoints LEAF les plus profonds d'un niveau (à travers les boxes). */
  const deepestOut = (level: any): string[] => {
    const eps = (level?.exitpoints ?? []).flatMap((ep: string) => {
      const epNode = _byId.get(ep);
      if (epNode && hasInner(epNode) && (epNode.vars?.inner?.exitpoints ?? []).length) return deepestOut(epNode.vars.inner);
      return [ep];
    });
    return eps;
  };
  const addIn = (targetId: string, sourceId: string): void => {
    const t = _byId.get(targetId);
    if (!t || !isVisible(t)) return;
    if (hasInner(t)) { addIn(t.vars.inner.entrypoint, sourceId); return; }
    if (!(t.vars?.in_group ?? []).includes(sourceId)) {
      t.vars = { ...(t.vars ?? {}), in_group: [...(t.vars?.in_group ?? []), sourceId] };
    }
  };
  const addOut = (targetId: string, targetNodeId: string): void => {
    const t = _byId.get(targetId);
    if (!t || !isVisible(t)) return;
    if (hasInner(t)) {
      for (const ep of t.vars.inner.exitpoints ?? []) addOut(ep, targetNodeId);
      return;
    }
    if (!(t.vars?.out_group ?? []).includes(targetNodeId)) {
      t.vars = { ...(t.vars ?? {}), out_group: [...(t.vars?.out_group ?? []), targetNodeId] };
    }
  };
  const route = (ctx: any): void => {
    for (const e of ctx.edges ?? []) {
      const a = nodeById(ctx, e.from), b = nodeById(ctx, e.to);
      if (!a || !b || !isVisible(a) || !isVisible(b)) continue;
      if (hasInner(b)) {
        // → box : l'arête entre par l'entrypoint (place) ; la SOURCE voit aussi
        // l'entrypoint LEAF en sortie (→ la transition `entry` entre main et
        // input devient foldable).
        addIn(b.vars.inner.entrypoint, e.from);
        const deep = deepestIn(b.vars.inner);
        const u = _byId.get(e.from);
        if (u && deep && !(u.vars?.out_group ?? []).includes(deep)) {
          u.vars = { ...(u.vars ?? {}), out_group: [...(u.vars?.out_group ?? []), deep] };
        }
      }
      if (hasInner(a)) {
        // box → : l'arête sort par les exitpoints ; la CIBLE voit aussi les
        // exitpoints LEAF en entrée.
        for (const ep of a.vars.inner.exitpoints ?? []) {
          addOut(ep, e.to);
          const w = _byId.get(e.to);
          if (w) {
            for (const deep of deepestOut(a.vars.inner)) {
              if (!(w.vars?.in_group ?? []).includes(deep)) {
                w.vars = { ...(w.vars ?? {}), in_group: [...(w.vars?.in_group ?? []), deep] };
              }
            }
          }
        }
      }
    }
    for (const n of ctx.nodes) if (n.vars?.inner?.nodes) route(n.vars.inner);
  };
  route(g);
  // 3) zippable : recheck (tous, y compris transitions) ; par via grouping.
  walkLevels(g, [], (ctx) => {
    for (const n of ctx.nodes) {
      if (isTokenPot(n)) n.vars = { ...(n.vars ?? {}), zippable: 'none' };
      else recheckNode(ctx, n.id);
    }
    const groups = new Map<string, string[]>();
    for (const t of ctx.nodes) {
      if (t.type !== 'transition' || !isVisible(t) || isTokenPot(t)) continue;
      if (!(t.vars?.in_group ?? []).length || !(t.vars?.out_group ?? []).length) continue;  // pas de groupe vide
      const k = gkey(t.vars?.in_group, t.vars?.out_group);
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k)!.push(t.id);
    }
    for (const trs of groups.values()) {
      if (trs.length < 2) continue;
      for (const id of trs) {
        const t = nodeById(ctx, id);
        if (t) t.vars = { ...(t.vars ?? {}), zippable: 'par' };
      }
    }
  });
}

/** Zippable d'un nœud, calculé depuis l'état maintenu (O(deg), single O(1)).
 *  Les BOX ne sont jamais seq/par (elles sont transparentes) — seule `single`
 *  s'applique aux boxes. Les POTs à token ne sont jamais clippables. */
function zippableOf(ctx: any, n: any): Zippable {
  if (!isVisible(n) || isTokenPot(n)) return 'none';
  const v = n.vars ?? {};
  // seq — TOUT nœud LEAF (place OU transition) en ligne droite : 1 entrée + 1
  // sortie (groupes box-transparents : les voisins sont des places/transitions,
  // routés à travers les boxes). Pureté via les COMPTEURS d'arêtes.
  const ins = v.in_group ?? [];
  const outs = v.out_group ?? [];
  if (!hasInner(n) && ins.length === 1 && outs.length === 1) {
    const x = _byId.get(ins[0]);
    const y = _byId.get(outs[0]);
    if (x && y && isVisible(x) && isVisible(y) && !isTokenPot(x) && !isTokenPot(y)
      && !hasInner(x) && !hasInner(y)
      && (x?.vars?.out_group ?? []).length === 1 && (y?.vars?.in_group ?? []).length === 1) {
      return 'seq';
    }
  }
  // single — box avec EXACTEMENT 1 place interne visible (LAZY : compteur).
  if (hasInner(n)) {
    if (typeof v.innerPlacesVisible === 'number' && v.innerPlacesVisible === 1) {
      const sp = n.vars.inner.nodes.find((x: any) => x.type !== 'transition' && isVisible(x));
      if (sp && !isTokenPot(sp)) return 'single';
    }
  }
  // par — voyage dans le graphe : un co-zippable partage une entrée avec N.
  if (n.type === 'transition' && (v.in_group ?? []).length && (v.out_group ?? []).length) {
    const key = gkey(v.in_group, v.out_group);
    for (const xid of v.in_group ?? []) {
      const x = _byId.get(xid);
      for (const mid of x?.vars?.out_group ?? []) {
        if (mid === n.id) continue;
        const m = _byId.get(mid);
        if (!m || !isVisible(m) || m.type !== 'transition') continue;
        if (gkey(m.vars?.in_group, m.vars?.out_group) === key) return 'par';
      }
    }
  }
  return 'none';
}

/** Recheck incrémental : recalcule `zippable` d'un nœud depuis l'état maintenu.
 *  À appeler à CHAQUE add/remove dans un in_group/out_group. */
export function recheckNode(ctx: any, id: string): void {
  const n = nodeById(ctx, id) ?? _byId.get(id);
  if (!n) return;
  const z = zippableOf(ctx, n);
  if (n.vars?.zippable !== z) n.vars = { ...(n.vars ?? {}), zippable: z };
}

/** Assure que l'état est présent (rebuild une seule fois si absent). */
function ensureState(g: any): void {
  const stale = g.nodes.some((n: any) => !n?.vars || !('zippable' in n.vars));
  if (stale) rebuildState(g);
  else if ((g as any).__mwById) _byId = (g as any).__mwById;
}

// ── Détection (récursive : top + boxes, nœuds visibles, jamais les tokens) ──

export function findClippable(g: { nodes: any[]; edges: any[] }, path: string[] = []): Clip[] {
  ensureState(g);
  const clips: Clip[] = [];
  const base = path.length ? `${path.join('/')}/` : '';
  const withPath = (c: Clip): Clip =>
    path.length ? { ...c, path, newId: `${path.join('/')}/${c.newId}` } : c;

  for (const n of g.nodes) {
    if (!isVisible(n) || isTokenPot(n)) continue;
    const z = n.vars?.zippable;
    if (z === 'seq') {
      const ins = n.vars.in_group ?? [];
      const outs = n.vars.out_group ?? [];
      if (ins.length !== 1 || outs.length !== 1) continue;
      clips.push(withPath({ type: 'seq', newId: `z:${n.id}`, zipped: [ins[0], n.id, outs[0]], on: [n.id], label: n.label ?? n.id }));
    } else if (z === 'single') {
      if (!hasInner(n)) continue;
      const sp = n.vars.inner.nodes.find((x: any) => x.type !== 'transition' && isVisible(x) && !isTokenPot(x));
      if (!sp) continue;
      clips.push({
        type: 'single', newId: `${base}s:${n.id}`, zipped: [sp.id], on: [n.id],
        label: n.label ?? n.id, path: [...path, n.id],
      });
    }
  }

  // par : groupes de transitions visibles à mêmes entrées ET sorties (non vides).
  const groups = new Map<string, string[]>();
  for (const t of g.nodes) {
    if (t.type !== 'transition' || !isVisible(t) || isTokenPot(t)) continue;
    if (t.vars?.zippable !== 'par') continue;
    const k = gkey(t.vars.in_group, t.vars.out_group);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(t.id);
  }
  for (const trs of groups.values()) {
    if (trs.length < 2) continue;
    clips.push(withPath({ type: 'par', newId: `p:${trs.join('+')}`, zipped: [...trs], on: [...trs], label: trs[0] }));
  }

  // Récursion dans les boxes.
  for (const n of g.nodes) {
    if (n.vars?.inner?.nodes) clips.push(...findClippable(n.vars.inner, [...path, n.id]));
  }

  return clips;
}

// ── Application dans le bon niveau (path) ───────────────────────────

/** Descend dans les boxes le long de `path` ; `info.boxId` = le box dont
 *  l'inner est le niveau atteint (null au top). */
function walkPath(g: any, path: string[] | undefined, fn: (ctx: any, info: { parent: any; boxId: string | null }) => void): void {
  let ctx = g, parent: any = null, boxId: string | null = null;
  for (const id of path ?? []) {
    const box = ctx.nodes.find((n: any) => n.id === id && n.vars?.inner?.nodes);
    if (!box) return;
    parent = ctx; boxId = id;
    ctx = box.vars.inner;
  }
  fn(ctx, { parent, boxId });
}

/** Niveau (container) où vit un nœud, via la chaîne vars.box. */
function levelOf(g: any, id: string): any {
  const node = _byId.get(id);
  if (!node) return g;
  const chain: string[] = [];
  let cur: any = node;
  while (cur?.vars?.box) { chain.unshift(cur.vars.box); cur = _byId.get(cur.vars.box); }
  let level: any = g;
  for (const b of chain) {
    const box = level.nodes.find((n: any) => n.id === b);
    level = box?.vars?.inner ?? level;
  }
  return level;
}

/** Niveau où créer le nœud z d'un clip :
 *  - si TOUS les zippés sont dans le même niveau (ctx), z y reste (dans la box) ;
 *  - si au moins un zippé est HORS de ce niveau, z est créé au niveau le plus
 *    EXTERNE parmi les zippés (il n'est pas dans la box). Même règle en par. */
function zCreateLevel(g: any, ctx: any, clip: Clip): any {
  if (clip.zipped.every((id: string) => nodeById(ctx, id))) return ctx;
  let best: any = null, bestDepth = Infinity;
  for (const id of clip.zipped) {
    const node = _byId.get(id);
    if (!node) continue;
    let depth = 0, cur: any = node;
    while (cur?.vars?.box) { depth++; cur = _byId.get(cur.vars.box); }
    if (depth < bestDepth) { bestDepth = depth; best = node; }
  }
  return best ? levelOf(g, best.id) : ctx;
}

/** Type du nœud généré par un clip :
 *  - SÉQUENTIEL : ALTERNANCE — fold sur une PLACE → transition ; fold sur une
 *    TRANSITION → place.
 *  - PARALLÈLE / single : transition (même type que ceux qu'on cache, avec les
 *    mêmes in/out). */
function zNodeType(clip: Clip): string {
  if (clip.type === 'seq') {
    const anchor = _byId.get(clip.zipped[1]);
    return anchor?.type === 'transition' ? 'place' : 'transition';
  }
  return 'transition';
}

/** Recheck UNE FOIS la foldabilité des nœuds affectés (to_maj) — uniquement
 *  ceux qui sont VISIBLES à la fin. */
function recheckVisible(g: any, toMaj: string[]): void {
  const seen = new Set<string>();
  for (const id of toMaj) {
    if (seen.has(id)) continue;
    seen.add(id);
    const n = _byId.get(id);
    if (!n || !isVisible(n)) continue;
    recheckNode(levelOf(g, id), id);
  }
}

/** Recalcule les compteurs d'une box (innerPlacesVisible / innerVisibleNodes)
 *  en scannant son inner (petit). */
function recountBox(box: any): void {
  if (!box?.vars?.inner?.nodes) return;
  const ipv = box.vars.inner.nodes.filter((x: any) => x.type !== 'transition' && isVisible(x)).length;
  const ivn = box.vars.inner.nodes.filter((x: any) => isVisible(x)).length;
  box.vars = { ...(box.vars ?? {}), innerPlacesVisible: ipv, innerVisibleNodes: ivn };
}

/** Redirige l'entrypoint/exitpoints d'une box vers z quand le fold a masqué
 *  l'ancien entry/exit — sinon le routage (fan-out) crée des arêtes fantômes
 *  vers les nœuds internes cachés. Ne s'applique que si z vit dans CETTE box. */
function redirectBoxEntryExit(box: any, zId: string, zipped: string[]): void {
  const inner = box?.vars?.inner;
  if (!inner) return;
  if (inner.entrypoint && zipped.includes(inner.entrypoint)) inner.entrypoint = zId;
  if ((inner.exitpoints ?? []).some((ep: string) => zipped.includes(ep))) {
    inner.exitpoints = inner.exitpoints.map((ep: string) => (zipped.includes(ep) ? zId : ep));
  }
}

/** ZIP TRANS-FRONTIÈRE : un clip seq dont les in/out traversent la frontière
 *  d'une box (ex. le contrat input→transition→output d'une skill). On crée z
 *  à l'EXTERIEUR, côté des entrées, branché sur main → output.
 *  ORDRE : 1) créer z, 2) créer ses arêtes, 3) METTRE À JOUR les in/out des
 *  nœuds liés (pour chaque arête créée). to_maj = nœuds affectés ; on ne
 *  recheck que les VISIBLES, une seule fois. La box N'EST PAS masquée (si plus
 *  aucune place interne visible, la règle show la retire de l'affichage). */
function zipAtCross(g: any, info: { parent: any; boxId: string | null }, clip: Clip): void {
  const src1 = clip.zipped[0];
  const src2 = clip.type === 'seq' ? clip.zipped[2] : clip.zipped[0];
  const x = _byId.get(src1), y = _byId.get(src2);
  const ins = x?.vars?.in_group ?? [];
  const outs = y?.vars?.out_group ?? [];
  const toMaj: string[] = [];
  // 1) créer z — DANS la box si tous les zippés y sont, sinon au niveau externe.
  const boxCtx = info.boxId ? nodeById(info.parent, info.boxId)?.vars?.inner : undefined;
  const zLevel = zCreateLevel(g, boxCtx ?? g, clip);
  const z = { id: clip.newId, type: zNodeType(clip), label: clip.label ?? clip.newId, vars: { visible: true, zipped: true, zippable: 'none', in_group: [...ins], out_group: [...outs] } };
  zLevel.nodes.push(z);
  _byId.set(z.id, z);
  toMaj.push(z.id);
  // z vit DANS la box englobante → rediriger son entrypoint/exitpoints si le
  // fold a masqué l'ancien (sinon arêtes fantômes vers les nœuds cachés).
  if (info.boxId) {
    const box = nodeById(info.parent, info.boxId);
    if (box && zLevel === box.vars?.inner) redirectBoxEntryExit(box, z.id, clip.zipped);
  }
  // 2) créer les arêtes (in → z, z → out) ; 3) maj in/out des nœuds liés.
  for (const s of ins) {
    zLevel.edges.push({ from: s, to: z.id, type: 'next' });
    const sn = _byId.get(s);
    if (sn) {
      const og = [...(sn.vars?.out_group ?? []).filter((v: string) => v !== z.id)];
      if (!og.includes(z.id)) og.push(z.id);
      sn.vars = { ...(sn.vars ?? {}), out_group: og };
    }
    toMaj.push(s);
  }
  for (const t of outs) {
    const tl = levelOf(g, t);
    tl.edges.push({ from: z.id, to: t, type: 'next' });
    const tn = _byId.get(t);
    if (tn) {
      const ig = [...(tn.vars?.in_group ?? []).filter((v: string) => v !== z.id)];
      if (!ig.includes(z.id)) ig.push(z.id);
      tn.vars = { ...(tn.vars ?? {}), in_group: ig };
    }
    toMaj.push(t);
  }
  // Masquer les zippés + les retirer des groupes + décrémenter les compteurs.
  for (const id of clip.zipped) {
    const n = _byId.get(id);
    if (!n) continue;
    for (const u of n.vars?.in_group ?? []) {
      const un = _byId.get(u);
      if (un) un.vars = { ...(un.vars ?? {}), out_group: (un.vars?.out_group ?? []).filter((v: string) => v !== id) };
      toMaj.push(u);
    }
    for (const w of n.vars?.out_group ?? []) {
      const wn = _byId.get(w);
      if (wn) wn.vars = { ...(wn.vars ?? {}), in_group: (wn.vars?.in_group ?? []).filter((v: string) => v !== id) };
      toMaj.push(w);
    }
    n.vars = { ...(n.vars ?? {}), visible: false, zippable: 'none', in_group: [], out_group: [] };
    if (n.vars?.box) {
      const box = _byId.get(n.vars.box);
      if (box) {
        recountBox(box);
        toMaj.push(n.vars.box);
      }
    }
  }
  // 4) recheck UNE FOIS, nœuds affectés VISIBLES.
  recheckVisible(g, toMaj);
}

function zipAt(g: any, ctx: any, info: { parent: any; boxId: string | null }, clip: Clip): void {
  const src1 = clip.zipped[0];
  const src2 = clip.type === 'seq' ? clip.zipped[2] : clip.zipped[0];
  const n1 = nodeById(ctx, src1), n2 = nodeById(ctx, src2);
  const ins = n1?.vars?.in_group ?? [];
  const outs = n2?.vars?.out_group ?? [];
  // Clip TRANS-FRONTIÈRE : in/out hors du niveau → zip cross (z à l'extérieur).
  const cross = !n1 || !n2
    || ins.some((id: string) => !nodeById(ctx, id))
    || outs.some((id: string) => !nodeById(ctx, id));
  if (cross) { zipAtCross(g, info, clip); return; }

  const toMaj: string[] = [];
  // ORDRE : d'abord créer z + ses arêtes, puis maj in/out, puis masquer.
  const z = { id: clip.newId, type: zNodeType(clip), label: clip.label ?? clip.newId, vars: { visible: true, zipped: true, zippable: 'none', in_group: [...ins], out_group: [...outs] } };
  ctx.nodes.push(z);
  _byId.set(z.id, z);
  toMaj.push(z.id);
  for (const s of ins) {
    ctx.edges.push({ from: s, to: z.id, type: 'next' });
    const sn = nodeById(ctx, s);
    if (sn) sn.vars = { ...(sn.vars ?? {}), out_group: [...(sn.vars?.out_group ?? []).filter((v: string) => !clip.zipped.includes(v)), z.id] };
    toMaj.push(s);
  }
  for (const t of outs) {
    ctx.edges.push({ from: z.id, to: t, type: 'next' });
    const tn = nodeById(ctx, t);
    if (tn) tn.vars = { ...(tn.vars ?? {}), in_group: [...(tn.vars?.in_group ?? []).filter((v: string) => !clip.zipped.includes(v)), z.id] };
    toMaj.push(t);
  }

  // Masquer les zippés + les retirer des groupes de leurs voisins.
  let delta = 0;
  for (const id of clip.zipped) {
    const n = nodeById(ctx, id);
    if (!n) continue;
    n.vars = { ...(n.vars ?? {}), visible: false };
    if (n.type !== 'transition' && !isTokenPot(n)) delta--;
    for (const u of n.vars?.in_group ?? []) {
      const un = nodeById(ctx, u);
      if (un) un.vars = { ...(un.vars ?? {}), out_group: (un.vars?.out_group ?? []).filter((v: string) => v !== id) };
      toMaj.push(u);
    }
    for (const w of n.vars?.out_group ?? []) {
      const wn = nodeById(ctx, w);
      if (wn) wn.vars = { ...(wn.vars ?? {}), in_group: (wn.vars?.in_group ?? []).filter((v: string) => v !== id) };
      toMaj.push(w);
    }
    n.vars = { ...n.vars, in_group: [], out_group: [], zippable: 'none' };
  }

  // Compteur lazy de la box englobante (nœud affecté).
  if (info.boxId) {
    const box = nodeById(info.parent, info.boxId);
    if (box) {
      redirectBoxEntryExit(box, z.id, clip.zipped);
      recountBox(box);
      toMaj.push(info.boxId);
    }
  }

  // Recheck UNE FOIS, nœuds affectés VISIBLES.
  recheckVisible(g, toMaj);
}

/** Applique un clip : crée le nœud (visible) + ses arêtes, rend invisibles les
 *  zippés. MAINTIENT l'état (groupes + compteurs + zippable) incrémentalement. */
export function zip(g: { nodes: any[]; edges: any[] }, clip: Clip): void {
  if ((g as any).__mwById) _byId = (g as any).__mwById;
  const before = debug_petri ? debugCount(g) : null;
  walkPath(g, clip.path, (ctx, info) => zipAt(g, ctx, info, clip));
  if (debug_petri && before) debugVerify(g, 'zip', before, clip);
}

function unzipAtCross(g: any, info: { parent: any; boxId: string | null }, clip: Clip): boolean {
  const z = _byId.get(clip.newId);
  if (!z || !isVisible(z)) return false;
  // Retirer z + ses arêtes (de TOUS les niveaux — les arêtes trans-frontières).
  const stripEdges = (level: any) => {
    level.edges = (level.edges ?? []).filter((e: any) => e.from !== z.id && e.to !== z.id);
    for (const n of level.nodes) if (n.vars?.inner?.nodes) stripEdges(n.vars.inner);
  };
  stripEdges(g);
  const zl = levelOf(g, z.id);
  if (zl) zl.nodes = zl.nodes.filter((n: any) => n.id !== z.id);
  // Restaurer les zippés (visible=true, la box redevient affichable si places).
  for (const id of clip.zipped) {
    const n = _byId.get(id);
    if (n) n.vars = { ...(n.vars ?? {}), visible: true };
  }
  rebuildState(g);
  return true;
}

function unzipAt(g: any, ctx: any, info: { parent: any; boxId: string | null }, clip: Clip): boolean {
  const z = _byId.get(clip.newId) ?? nodeById(ctx, clip.newId);
  if (!z || !isVisible(z)) return false;
  // Unzip trans-frontière : z hors du niveau ctx OU un zippé est dans un autre
  // niveau → restauration via l'index global + rebuildState.
  const local = nodeById(ctx, clip.newId) != null && clip.zipped.every((id: string) => nodeById(ctx, id));
  if (!local) return unzipAtCross(g, info, clip);

  const toMaj: string[] = [];
  const zIns = z.vars?.in_group ?? [];
  const zOuts = z.vars?.out_group ?? [];

  // 1) Supprimer z (+ arêtes liées, + groupes voisins).
  ctx.nodes = ctx.nodes.filter((n: any) => n.id !== z.id);
  ctx.edges = ctx.edges.filter((e: any) => e.from !== z.id && e.to !== z.id);
  for (const s of zIns) {
    const sn = nodeById(ctx, s);
    if (sn) sn.vars = { ...(sn.vars ?? {}), out_group: (sn.vars?.out_group ?? []).filter((v: string) => v !== z.id) };
    toMaj.push(s);
  }
  for (const t of zOuts) {
    const tn = nodeById(ctx, t);
    if (tn) tn.vars = { ...(tn.vars ?? {}), in_group: (tn.vars?.in_group ?? []).filter((v: string) => v !== z.id) };
    toMaj.push(t);
  }

  // 2) Restaurer les zippés (visibles).
  let delta = 0;
  for (const id of clip.zipped) {
    const n = nodeById(ctx, id);
    if (!n) continue;
    n.vars = { ...(n.vars ?? {}), visible: true };
    if (n.type !== 'transition' && !isTokenPot(n)) delta++;
  }

  // 3) Regrouper les zippés avec leurs voisins visibles (ré-ajout groupes).
  for (const id of clip.zipped) {
    const n = nodeById(ctx, id);
    if (!n) continue;
    const { ins, outs } = groupsFromEdges(ctx, id);
    n.vars = { ...(n.vars ?? {}), in_group: ins, out_group: outs };
    for (const u of ins) {
      const un = nodeById(ctx, u);
      if (un) un.vars = { ...(un.vars ?? {}), out_group: [...(un.vars?.out_group ?? []).filter((v: string) => v !== id), id] };
      toMaj.push(u);
    }
    for (const w of outs) {
      const wn = nodeById(ctx, w);
      if (wn) wn.vars = { ...(wn.vars ?? {}), in_group: [...(wn.vars?.in_group ?? []).filter((v: string) => v !== id), id] };
      toMaj.push(w);
    }
    toMaj.push(id);
  }

  // 4) Compteur lazy du box + recheck du box.
  if (info.boxId) {
    const box = nodeById(info.parent, info.boxId);
    if (box) {
      recountBox(box);
      toMaj.push(info.boxId);
    }
  }

  // 5) Recheck UNE FOIS, nœuds affectés VISIBLES.
  recheckVisible(g, toMaj);
  return true;
}

/** Inverse un clip : supprime le nœud créé (+ arêtes liées), rend visibles les
 *  zippés. Ne s'applique que si le nœud créé est encore visible. */
export function unzip(g: { nodes: any[]; edges: any[] }, clip: Clip): boolean {
  if ((g as any).__mwById) _byId = (g as any).__mwById;
  const before = debug_petri ? debugCount(g) : null;
  let ok = false;
  walkPath(g, clip.path, (ctx, info) => { if (unzipAt(g, ctx, info, clip)) ok = true; });
  if (debug_petri && before && ok) debugVerify(g, 'unzip', before, clip);
  return ok;
}

// ── DEBUG (debug_petri) ─────────────────────────────────────────────
// debug_petri = 1 : à chaque zip/unzip, on compte les places/transitions/
// arêtes VISIBLES avant et après, on vérifie les invariants (deltas attendus,
// somme in == somme out, aucune box liée, aucune arête vers un invisible) et
// on refait une foldabilité COMPLÈTE sur une copie du graphe (state vidé +
// rebuildState) — toute divergence est loggée.

export let debug_petri = 0;

function debugCount(g: any) {
  let places = 0, transitions = 0, edges = 0, sumIn = 0, sumOut = 0;
  const walk = (nodes: any[], levelEdges: any[]) => {
    for (const n of nodes ?? []) {
      if (n.vars?.visible === false) continue;
      if (hasInner(n)) continue;                 // les box ne sont ni place ni transition
      if (n.type === 'transition') transitions++; else places++;
      sumIn += (n.vars?.in_group ?? []).length;
      sumOut += (n.vars?.out_group ?? []).length;
      if (n.vars?.inner?.nodes) walk(n.vars.inner.nodes, n.vars.inner.edges ?? []);
    }
    for (const e of levelEdges ?? []) {
      const a = _byId.get(e.from), b = _byId.get(e.to);
      if (!a || !b || !isVisible(a) || !isVisible(b)) continue;
      if (hasInner(a) || hasInner(b)) continue;  // une box n'est jamais reliée
      edges++;
    }
  };
  walk(g.nodes, g.edges ?? []);
  return { places, transitions, edges, sumIn, sumOut };
}

function debugExpectedDeltas(clip: Clip) {
  let places = 0, transitions = 0;
  for (const id of clip.zipped) {
    const n = _byId.get(id);
    if (!n) continue;
    if (n.type === 'transition') transitions--; else places--;
  }
  const z = _byId.get(clip.newId);
  if (z) { if (z.type === 'transition') transitions++; else places++; }
  let edges = 0;
  if (clip.type === 'seq') {
    edges = -2;                                  // place -1, transition -1, arête -2
  } else if (clip.type === 'par') {
    const n = clip.zipped.length;
    const k = z?.vars?.in_group?.length ?? 0;
    const m = z?.vars?.out_group?.length ?? 0;
    edges = -(n - 1) * (k + m);
  } else {                                       // single : p1 remplacé par z, mêmes degrés
    const p = clip.zipped[0] ? _byId.get(clip.zipped[0]) : null;
    const k = p?.vars?.in_group?.length ?? 0;
    const m = p?.vars?.out_group?.length ?? 0;
    edges = -((k + m) - (k + m));                // = 0
  }
  return { places, transitions, edges };
}

function debugNoBoxEdges(g: any): string[] {
  const bad: string[] = [];
  const boxes = new Set<string>();
  const walkB = (nodes: any[]) => { for (const n of nodes) { if (hasInner(n)) boxes.add(n.id); if (n.vars?.inner?.nodes) walkB(n.vars.inner.nodes); } };
  walkB(g.nodes);
  const walk = (nodes: any[]) => {
    for (const n of nodes) {
      if (hasInner(n)) {
        if ((n.vars?.in_group ?? []).length || (n.vars?.out_group ?? []).length) bad.push(`box ${n.id} porte des in/out`);
      } else if (n.vars?.visible !== false) {
        for (const u of n.vars?.in_group ?? []) if (boxes.has(u)) bad.push(`${n.id}.in contient la box ${u}`);
        for (const w of n.vars?.out_group ?? []) if (boxes.has(w)) bad.push(`${n.id}.out contient la box ${w}`);
      }
      if (n.vars?.inner?.nodes) walk(n.vars.inner.nodes);
    }
  };
  walk(g.nodes);
  return bad;
}

function debugFoldability(g: any): string[] {
  let copy: any;
  try { copy = JSON.parse(JSON.stringify(g)); } catch { return []; }
  const strip = (nodes: any[]) => {
    for (const n of nodes) {
      if (n.vars) {
        delete n.vars.in_group; delete n.vars.out_group; delete n.vars.in_edges; delete n.vars.out_edges;
        delete n.vars.zippable; delete n.vars.__mwById;
        delete n.vars.innerPlacesVisible; delete n.vars.innerVisibleNodes; delete n.vars.box;
      }
      if (n.vars?.inner?.nodes) strip(n.vars.inner.nodes);
    }
  };
  strip(copy.nodes);
  try { rebuildState(copy); } catch (e) { return [`rebuild échoué: ${e}`]; }
  const idx = new Map<string, any>();
  const idxWalk = (nodes: any[]) => { for (const n of nodes) { idx.set(n.id, n); if (n.vars?.inner?.nodes) idxWalk(n.vars.inner.nodes); } };
  idxWalk(copy.nodes);
  const bad: string[] = [];
  const walk = (nodes: any[]) => {
    for (const n of nodes) {
      const c = idx.get(n.id);
      if (c && c.vars?.zippable !== n.vars?.zippable) bad.push(`${n.id}: main=${n.vars?.zippable} recalculé=${c.vars?.zippable}`);
      if (n.vars?.inner?.nodes) walk(n.vars.inner.nodes);
    }
  };
  walk(g.nodes);
  return bad;
}

function debugVerify(g: any, op: string, before: any, clip: Clip): void {
  const after = debugCount(g);
  const exp = debugExpectedDeltas(clip);
  if (op === 'unzip') { exp.places = -exp.places; exp.transitions = -exp.transitions; exp.edges = -exp.edges; }
  const dPlaces = after.places - before.places;
  const dTrans = after.transitions - before.transitions;
  const dEdges = after.edges - before.edges;
  const issues: string[] = [];
  if (dPlaces !== exp.places) issues.push(`places attendu ${exp.places}, obtenu ${dPlaces}`);
  if (dTrans !== exp.transitions) issues.push(`transitions attendu ${exp.transitions}, obtenu ${dTrans}`);
  if (dEdges !== exp.edges) issues.push(`arêtes attendu ${exp.edges}, obtenu ${dEdges}`);
  if (after.sumIn !== after.sumOut) issues.push(`sum in(${after.sumIn}) != sum out(${after.sumOut})`);
  issues.push(...debugNoBoxEdges(g));
  const fold = debugFoldability(g);
  if (fold.length) issues.push(`foldabilité recalculée: ${fold.slice(0, 6).join('; ')}${fold.length > 6 ? `… (+${fold.length - 6})` : ''}`);
  if (issues.length) {
    console.error(`[debug_petri] ${op} ${clip.type} on=[${clip.on}] zipped=[${clip.zipped}]${clip.path?.length ? ` @${clip.path.join('/')}` : ''}`);
    console.error(`  avant: places=${before.places} trans=${before.transitions} arêtes=${before.edges}`);
    console.error(`  après: places=${after.places} trans=${after.transitions} arêtes=${after.edges} sumIn=${after.sumIn} sumOut=${after.sumOut}`);
    for (const i of issues) console.error(`  ✗ ${i}`);
  } else if (debug_petri >= 2) {
    console.log(`[debug_petri] ${op} ${clip.type} ok: places ${before.places}→${after.places} (Δ${dPlaces}), trans ${before.transitions}→${after.transitions} (Δ${dTrans}), arêtes ${before.edges}→${after.edges} (Δ${dEdges})`);
  }
}

/** zip_all : clippe tout ce qui est clippable (nœuds visibles, tous niveaux). */
export function zipAll(g: { nodes: any[]; edges: any[] }): ClipTable {
  const table = createClipTable();
  let guard = 0;
  while (guard++ < 500) {
    const clips = findClippable(g);
    if (!clips.length) break;
    const clip = clips[0];
    zip(g, clip);
    table.clips.push(clip);
  }
  return table;
}

/** unzip_all : dé-clippe (ordre inverse), uniquement les nœuds créés visibles. */
export function unzipAll(g: { nodes: any[]; edges: any[] }, table: ClipTable): void {
  for (const clip of [...table.clips].reverse()) {
    unzip(g, clip);
  }
  table.clips = [];
}
