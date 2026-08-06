// Opérations de mutation du layout — PURES (retournent un nouveau layout,
// ne mutent jamais l'entrée). Testables sans UI.
//
// Règle d'or : aucune opération ne pose un panel "nu". Un panel est toujours
// porté par un onglet (group.tabs[]). Un groupe a au moins 1 onglet ; un
// groupe vidé est supprimé de l'arbre. Un mini-layout a un sous-arbre non vide.

import type { GroupNode, Layout, PanelOcc, MiniLayoutNode, SplitNode, TreeNode } from './types.ts';
import { genId } from './types.ts';

// ── Helpers internes (immutables) ────────────────────────────────────

/** Clone profond (JSON) — simple et suffisant pour des données pures. */
export function cloneLayout(layout: Layout): Layout {
  return JSON.parse(JSON.stringify(layout)) as Layout;
}

function makeGroup(tabs: PanelOcc[], active?: string): GroupNode {
  const first = active ?? tabs[0]?.occId ?? '';
  return { type: 'group', id: genId('pg'), tabs, active: first };
}

function makeSplit(direction: 'horizontal' | 'vertical', children: TreeNode[]): SplitNode {
  return { type: 'split', id: genId('sp'), direction, children, sizes: children.map(() => 50) };
}

/** Ajoute un onglet à un groupe (nouveau tableau). */
function groupWithTab(group: GroupNode, occ: PanelOcc): GroupNode {
  return { ...group, tabs: [...group.tabs, occ], active: occ.occId };
}

/** Retire un onglet d'un groupe ; si le groupe se vide, le nœud est remplacé. */
function removeTab(tree: TreeNode, groupId: string, occId: string): TreeNode | null {
  if (tree.type === 'group' && tree.id === groupId) {
    const tabs = tree.tabs.filter((t) => t.occId !== occId);
    if (tabs.length === 0) return null; // groupe vidé → supprimé
    const active = tree.active === occId ? tabs[0].occId : tree.active;
    return { ...tree, tabs, active };
  }
  if (tree.type === 'split') {
    const children: TreeNode[] = [];
    for (const child of tree.children) {
      const r = removeTab(child, groupId, occId);
      if (r) children.push(r);
    }
    if (children.length === 0) return null;
    if (children.length === 1) return children[0];
    return { ...tree, children };
  }
  if (tree.type === "miniLayout") {
    const r = removeTab(tree.tree, groupId, occId);
    if (!r) return null;
    return { ...tree, tree: r };
  }
  return tree;
}

/** Trouve le groupe contenant un occId. */
export function findGroup(tree: TreeNode, occId: string): GroupNode | null {
  if (tree.type === 'group') {
    if (tree.tabs.some((t) => t.occId === occId)) return tree;
    return null;
  }
  if (tree.type === 'split') {
    for (const c of tree.children) {
      const r = findGroup(c, occId);
      if (r) return r;
    }
    return null;
  }
  if (tree.type === "miniLayout") return findGroup(tree.tree, occId);
  return null;
}

/** Trouve le groupe par son id. */
export function findGroupById(tree: TreeNode, groupId: string): GroupNode | null {
  if (tree.type === 'group') return tree.id === groupId ? tree : null;
  if (tree.type === 'split') {
    for (const c of tree.children) {
      const r = findGroupById(c, groupId);
      if (r) return r;
    }
    return null;
  }
  if (tree.type === "miniLayout") return findGroupById(tree.tree, groupId);
  return null;
}

// ── Unicité d'un panel (par fenêtre, params identiques) ───────────────

/**
 * Compare deux paramètres (deep equality, insensible à l'ordre des clés).
 * undefined / absent et {} sont considérés équivalents.
 */
export function paramsEqual(a?: Record<string, any>, b?: Record<string, any>): boolean {
  const na = a ?? {};
  const nb = b ?? {};
  const ka = Object.keys(na).sort();
  const kb = Object.keys(nb).sort();
  if (ka.length !== kb.length) return false;
  for (let i = 0; i < ka.length; i++) {
    if (ka[i] !== kb[i]) return false;
    if (!deepEqual(na[ka[i]], nb[kb[i]])) return false;
  }
  return true;
}

function deepEqual(a: any, b: any): boolean {
  if (a === b) return true;
  if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    if (a.length !== b.length) return false;
    return a.every((v, i) => deepEqual(v, b[i]));
  }
  const ka = Object.keys(a).sort();
  const kb = Object.keys(b).sort();
  if (ka.length !== kb.length) return false;
  for (const k of ka) {
    if (!Object.prototype.hasOwnProperty.call(b, k)) return false;
    if (!deepEqual(a[k], b[k])) return false;
  }
  return true;
}

/**
 * Cherche un panel (id + params identiques) dans TOUT l'arbre d'une fenêtre
 * (groupes + mini-layouts). Retourne { groupe, occurrence } ou null.
 * Règle : un panel ne peut exister qu'en un seul exemplaire par fenêtre ;
 * des paramètres différents ⇒ panel différent (exemplaire autorisé).
 */
export function findPanelOccurrence(
  tree: TreeNode,
  panelId: string,
  params?: Record<string, any>,
): { group: GroupNode; occ: PanelOcc } | null {
  if (tree.type === 'group') {
    const occ = tree.tabs.find((t) => t.panel === panelId && paramsEqual(t.params, params));
    return occ ? { group: tree, occ } : null;
  }
  if (tree.type === "miniLayout") {
    return findPanelOccurrence(tree.tree, panelId, params);
  }
  if (tree.type === 'split') {
    for (const c of tree.children) {
      const r = findPanelOccurrence(c, panelId, params);
      if (r) return r;
    }
  }
  return null;
}

/**
 * Liste les panels déjà présents dans la fenêtre (id + params), tous
 * sous-layouts (groupes + mini-layouts) compris. Pour le filtre du catalogue.
 */
export function listPresentPanels(tree: TreeNode): { panel: string; params?: Record<string, any> }[] {
  const out: { panel: string; params?: Record<string, any> }[] = [];
  const walk = (n: TreeNode): void => {
    if (n.type === 'group') {
      for (const t of n.tabs) out.push({ panel: t.panel, params: t.params });
    } else if (n.type === "miniLayout") {
      walk(n.tree);
    } else {
      for (const c of n.children) walk(c);
    }
  };
  walk(tree);
  return out;
}

// ── Opérations ───────────────────────────────────────────────────────

/**
 * Ajoute un panel dans un groupe (créé s'il n'existe pas, ou enraciné).
 * Le panel est TOUJOURS dans un onglet.
 */
export function addPanel(layout: Layout, groupId: string | null, panelId: string, params?: Record<string, any>): Layout {
  const next = cloneLayout(layout);

  // Unicité par fenêtre (params identiques) : on active l'existant, pas de doublon.
  const existing = findPanelOccurrence(next.tree, panelId, params);
  if (existing) {
    return activateTab(next, existing.group.id, existing.occ.occId);
  }

  const occ: PanelOcc = { panel: panelId, occId: genId('occ'), ...(params ? { params } : {}) };
  let root = next.tree;

  if (!groupId) {
    // Pas de groupe cible → crée un groupe racine (ou ajoute au groupe racine unique).
    if (root.type === 'group') {
      root = groupWithTab(root, occ);
    } else {
      root = makeSplit('vertical', [makeGroup([occ]), root]);
    }
  } else {
    const group = findGroupById(root, groupId);
    if (!group) {
      // Groupe cible introuvable → on enracine en mini-layout ? Non : on crée un groupe racine.
      root = makeSplit('vertical', [makeGroup([occ]), root]);
    } else {
      // Réutilise le groupe cible en poussant l'onglet.
      root = replaceNode(root, group.id, groupWithTab(group, occ));
    }
  }
  return { ...next, tree: root };
}

/** Remplace un nœud par id (groupe/mini-layout/split) dans l'arbre. */
function replaceNode(tree: TreeNode, id: string, replacement: TreeNode): TreeNode {
  if (tree.type === 'group') return tree.id === id ? replacement : tree;
  if (tree.type === "miniLayout") return tree.id === id ? replacement : { ...tree, tree: replaceNode(tree.tree, id, replacement) };
  if (tree.type === 'split') {
    return { ...tree, children: tree.children.map((c) => replaceNode(c, id, replacement)) };
  }
  return tree;
}

/** Ferme un onglet ; supprime le groupe s'il se vide. */
export function closeTab(layout: Layout, groupId: string, occId: string): Layout {
  const next = cloneLayout(layout);
  const tree = removeTab(next.tree, groupId, occId);
  if (!tree) return { ...next, tree: makeGroup([], genId()) }; // arbre vide → groupe vide (invariant: ≥1 onglet)
  // Si l'arbre est un split vide → groupe minimal
  return { ...next, tree };
}

/** Change l'onglet actif d'un groupe. */
export function activateTab(layout: Layout, groupId: string, occId: string): Layout {
  const next = cloneLayout(layout);
  const group = findGroupById(next.tree, groupId);
  if (!group || !group.tabs.some((t) => t.occId === occId)) return layout;
  const updated: GroupNode = { ...group, active: occId };
  return { ...next, tree: replaceNode(next.tree, groupId, updated) };
}

/**
 * Déplace un onglet d'un groupe vers un autre (à une position donnée, ou à la fin).
 */
export function moveTab(
  layout: Layout,
  fromGroupId: string,
  toGroupId: string,
  occId: string,
  index?: number,
): Layout {
  const next = cloneLayout(layout);
  const from = findGroupById(next.tree, fromGroupId);
  if (!from) return layout;
  const occ = from.tabs.find((t) => t.occId === occId);
  if (!occ) return layout;

  // 1. retire l'onglet du groupe source (et le groupe si vide)
  let tree = removeTab(next.tree, fromGroupId, occId);
  if (!tree) tree = makeGroup([occ], occ.occId);

  // 2. ajoute au groupe cible
  const to = findGroupById(tree, toGroupId);
  if (to) {
    const tabs = [...to.tabs];
    if (index !== undefined) tabs.splice(Math.min(index, tabs.length), 0, occ);
    else tabs.push(occ);
    const updated: GroupNode = { ...to, tabs, active: occ.occId };
    tree = replaceNode(tree, toGroupId, updated);
  } else {
    // groupe cible introuvable (a été vidé) → enracine l'onglet
    tree = makeSplit('vertical', [makeGroup([occ]), tree]);
  }
  return { ...next, tree };
}

/**
 * Split d'un groupe : retire l'onglet du groupe source et le place dans une
 * nouvelle zone (direction) adjacente au groupe cible.
 */
export function splitGroup(
  layout: Layout,
  leafId: string,
  direction: 'horizontal' | 'vertical',
  occId: string,
  fromGroupId?: string,
): Layout {
  const next = cloneLayout(layout);
  const sourceId = fromGroupId ?? findGroup(next.tree, occId)?.id ?? '';
  const from = findGroupById(next.tree, sourceId);
  if (!from) return layout;
  const occ = from.tabs.find((t) => t.occId === occId);
  if (!occ) return layout;

  // retire l'onglet source
  let tree = removeTab(next.tree, sourceId, occId);
  if (!tree) tree = makeGroup([occ], occ.occId);

  // cible : le groupe leafId (ou le groupe source s'il reste)
  const targetGroup = findGroupById(tree, leafId);
  if (!targetGroup) return layout;

  const newGroup = makeGroup([occ]);

  // insère newGroup à côté de targetGroup dans le split parent (ou crée un split)
  tree = insertSibling(tree, targetGroup, newGroup, direction);
  return { ...next, tree };
}

/** Insère newGroup à côté de target dans l'arbre, dans la direction donnée. */
function insertSibling(tree: TreeNode, target: TreeNode, newGroup: TreeNode, direction: 'horizontal' | 'vertical'): TreeNode {
  if (tree.type === 'group') {
    return makeSplit(direction, [tree, newGroup]);
  }
  if (tree.type === "miniLayout") {
    return { ...tree, tree: insertSibling(tree.tree, target, newGroup, direction) };
  }
  // split
  const targetId = (target as any).id;
  const idx = tree.children.findIndex((c) => c === target || (c as any).id === targetId);
  if (idx === -1) {
    // chercher dans les sous-arbres
    const children = tree.children.map((c) => insertSibling(c, target, newGroup, direction));
    return { ...tree, children };
  }
  if (tree.direction === direction) {
    const children = [...tree.children];
    children.splice(idx + 1, 0, newGroup);
    return { ...tree, children, sizes: children.map(() => 50) };
  }
  const children = [...tree.children];
  const pair: SplitNode = makeSplit(direction, [children[idx], newGroup]);
  children[idx] = pair;
  return { ...tree, children, sizes: children.map(() => 50) };
}

/** Réordonne les onglets d'un groupe (par liste d'occId). */
export function reorderTabs(layout: Layout, groupId: string, order: string[]): Layout {
  const next = cloneLayout(layout);
  const group = findGroupById(next.tree, groupId);
  if (!group) return layout;
  const byId = new Map(group.tabs.map((t) => [t.occId, t]));
  const tabs = order.map((id) => byId.get(id)).filter(Boolean) as PanelOcc[];
  if (tabs.length !== group.tabs.length) return layout;
  const active = group.tabs.some((t) => t.occId === group.active) ? group.active : tabs[0].occId;
  const updated: GroupNode = { ...group, tabs, active };
  return { ...next, tree: replaceNode(next.tree, groupId, updated) };
}

/**
 * Redimensionne les enfants d'un split (drag du séparateur).
 * @param index index du séparateur (entre children[index] et children[index+1])
 * @param delta fraction [0..1] de déplacement (signé, proportionnel au split)
 * @param totalPx taille totale du split en pixels (optionnel, pour un delta relatif)
 */
export function resizeSplit(layout: Layout, splitId: string, index: number, delta: number, totalPx?: number): Layout {
  const next = cloneLayout(layout);
  const split = findSplitById(next.tree, splitId);
  if (!split) return layout;
  const n = split.children.length;
  if (index < 0 || index >= n - 1) return layout;

  let sizes = split.sizes && split.sizes.length === n ? [...split.sizes] : Array(n).fill(100 / n);
  // delta en fraction du total [0..1] → converti en points de pourcentage.
  const step = totalPx && totalPx > 0 ? (delta / totalPx) * 100 : delta * 100;
  const left = sizes[index];
  const right = sizes[index + 1];
  const dl = Math.max(-left + 5, Math.min(step, right - 5));
  sizes[index] = left + dl;
  sizes[index + 1] = right - dl;

  const updated: SplitNode = { ...split, sizes };
  return { ...next, tree: replaceNodeById(next.tree, splitId, updated) };
}

/** Trouve un split par son id. */
function findSplitById(tree: TreeNode, splitId: string): SplitNode | null {
  if (tree.type === 'split') {
    if (tree.id === splitId) return tree;
    for (const c of tree.children) {
      const r = findSplitById(c, splitId);
      if (r) return r;
    }
    return null;
  }
  if (tree.type === "miniLayout") return findSplitById(tree.tree, splitId);
  return null;
}

/** Remplace un nœud (split) par id — variante pour les splits. */
function replaceNodeById(tree: TreeNode, id: string, replacement: TreeNode): TreeNode {
  if (tree.type === 'split') {
    if (tree.id === id) return replacement;
    return { ...tree, children: tree.children.map((c) => replaceNodeById(c, id, replacement)) };
  }
  if (tree.type === "miniLayout") return { ...tree, tree: replaceNodeById(tree.tree, id, replacement) };
  return tree;
}

/**
 * Ajoute un mini-layout (sous-arbre à 1 onglet) dans un groupe.
 * C'est le concept "fake-window" : un layout imbriqué avec son propre onglet.
 */
export function addMiniLayout(layout: Layout, groupId: string, panelId: string, params?: Record<string, any>): Layout {
  const next = cloneLayout(layout);

  // Unicité par fenêtre (params identiques) : déjà présent → pas de doublon.
  if (findPanelOccurrence(next.tree, panelId, params)) {
    return next;
  }

  const occ: PanelOcc = { panel: panelId, occId: genId('occ'), ...(params ? { params } : {}) };
  const inner = makeGroup([occ]);
  const mini: MiniLayoutNode = { type: "miniLayout", id: genId('mini'), tree: inner };
  const group = findGroupById(next.tree, groupId);
  if (!group) {
    return { ...next, tree: makeSplit('vertical', [mini, next.tree]) };
  }
  // Insère le nœud mini-layout À CÔTÉ du groupe (le groupe reste intact) : le
  // mini-layout est un panneau conteneur qui emballe un sous-arbre.
  const inserted = insertSibling(next.tree, group, mini, 'vertical');
  return { ...next, tree: inserted };
}

/**
 * Supprime un mini-layout (et son sous-arbre) de l'arbre.
 */
export function closeMiniLayout(layout: Layout, miniLayoutId: string): Layout {
  const next = cloneLayout(layout);
  const tree = removeMiniLayout(next.tree, miniLayoutId);
  if (!tree) return { ...next, tree: makeGroup([], genId()) };
  return { ...next, tree };
}

function removeMiniLayout(tree: TreeNode, miniLayoutId: string): TreeNode | null {
  if (tree.type === "miniLayout") {
    if (tree.id === miniLayoutId) return null;
    const r = removeMiniLayout(tree.tree, miniLayoutId);
    if (!r) return null;
    return { ...tree, tree: r };
  }
  if (tree.type === 'split') {
    const children: TreeNode[] = [];
    for (const child of tree.children) {
      const r = removeMiniLayout(child, miniLayoutId);
      if (r) children.push(r);
    }
    if (children.length === 0) return null;
    if (children.length === 1) return children[0];
    return { ...tree, children };
  }
  return tree;
}

// ── Invariants (validation) ──────────────────────────────────────────

/** Vérifie les invariants du layout. Retourne la liste des violations. */
export function validateLayout(layout: Layout): string[] {
  const errors: string[] = [];
  const seenOcc = new Set<string>();

  function walk(tree: TreeNode, path: string) {
    if (tree.type === 'group') {
      if (tree.tabs.length === 0) errors.push(`${path}: groupe vide (invariant: ≥1 onglet)`);
      if (!tree.tabs.some((t) => t.occId === tree.active)) {
        if (tree.tabs.length > 0) errors.push(`${path}: active '${tree.active}' introuvable`);
      }
      for (const t of tree.tabs) {
        if (seenOcc.has(t.occId)) errors.push(`${path}: occId dupliqué '${t.occId}'`);
        seenOcc.add(t.occId);
      }
    } else if (tree.type === "miniLayout") {
      if (!tree.tree) errors.push(`${path}: mini-layout '${tree.id}' sans sous-arbre`);
      else walk(tree.tree, `${path}.miniLayout[${tree.id}]`);
    } else {
      if (tree.children.length === 0) errors.push(`${path}: split vide`);
      for (let i = 0; i < tree.children.length; i++) walk(tree.children[i], `${path}[${i}]`);
    }
  }
  walk(layout.tree, 'root');
  return errors;
}
