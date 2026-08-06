// Opérations de mutation du layout — PURES (retournent un nouveau layout,
// ne mutent jamais l'entrée). Testables sans UI.
//
// Règle d'or : aucune opération ne pose un panel "nu". Un panel est toujours
// porté par un onglet (group.tabs[]). Un groupe a au moins 1 onglet ; un
// groupe vidé est supprimé de l'arbre. Un mini-layout a un sous-arbre non vide.

import type { GroupNode, Layout, PanelOcc, SplitNode, TreeNode } from './types.ts';
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
  if (tree.type === 'group') {
    let tabs = tree.tabs;
    if (tree.id === groupId) {
      tabs = tabs.filter((t) => t.occId !== occId);
      if (tabs.length === 0) return null; // groupe vidé → supprimé
    }
    // mini-layouts : retirer l'occId dans le sous-arbre des onglets qui en ont,
    // sans jamais supprimer l'onglet mini-layout lui-même (le groupe principal
    // ne se vide donc jamais quand l'occId est DANS un mini-layout).
    let changed = false;
    const mapped = tabs.map((t) => {
      if (!t.tree || t.occId === occId) return t;
      if (!findGroupById(t.tree, groupId)) return t; // cible absente de ce sous-arbre
      const r = removeTab(t.tree, groupId, occId);
      if (r === t.tree) return t;
      changed = true;
      // mini-layout vidé → on garde le conteneur avec un groupe vide (l'onglet
      // reste un mini-layout prêt à accueillir d'autres panels).
      return { ...t, tree: r ?? { type: 'group', id: genId('pg'), tabs: [], active: '' } };
    });
    const active = tree.active === occId ? mapped[0].occId : tree.active;
    return changed || tree.id === groupId ? { ...tree, tabs: mapped, active } : tree;
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
  return tree;
}

/** Trouve le groupe contenant un occId (y compris dans les onglets mini-layout). */
export function findGroup(tree: TreeNode, occId: string): GroupNode | null {
  if (tree.type === 'group') {
    if (tree.tabs.some((t) => t.occId === occId)) return tree;
    // mini-layouts : chercher dans le sous-arbre des onglets qui en ont
    for (const t of tree.tabs) {
      if (t.tree) {
        const r = findGroup(t.tree, occId);
        if (r) return r;
      }
    }
    return null;
  }
  if (tree.type === 'split') {
    for (const c of tree.children) {
      const r = findGroup(c, occId);
      if (r) return r;
    }
    return null;
  }
  return null;
}

/** Trouve le groupe par son id. */
export function findGroupById(tree: TreeNode, groupId: string): GroupNode | null {
  if (tree.type === 'group') {
    if (tree.id === groupId) return tree;
    // mini-layouts : chercher dans le sous-arbre des onglets qui en ont
    for (const t of tree.tabs) {
      if (t.tree) {
        const r = findGroupById(t.tree, groupId);
        if (r) return r;
      }
    }
    return null;
  }
  if (tree.type === 'split') {
    for (const c of tree.children) {
      const r = findGroupById(c, groupId);
      if (r) return r;
    }
    return null;
  }
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
    if (occ) return { group: tree, occ };
    // mini-layouts : chercher aussi dans le sous-arbre des onglets qui en ont
    for (const t of tree.tabs) {
      if (t.tree) {
        const r = findPanelOccurrence(t.tree, panelId, params);
        if (r) return r;
      }
    }
    return null;
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
      for (const t of n.tabs) {
        out.push({ panel: t.panel, params: t.params });
        if (t.tree) walk(t.tree);
      }
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
  if (tree.type === 'group') {
    if (tree.id === id) return replacement;
    // mini-layouts : remplacer aussi dans le sous-arbre des onglets qui en ont
    let changed = false;
    const tabs = tree.tabs.map((t) => {
      if (!t.tree) return t;
      const r = replaceNode(t.tree, id, replacement);
      if (r === t.tree) return t;
      changed = true;
      return { ...t, tree: r };
    });
    return changed ? { ...tree, tabs } : tree;
  }
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
 * Renomme un onglet (titre personnalisé `label`). Un label vide/null le retire
 * (retour au label par défaut du panel).
 */
export function renameTab(layout: Layout, groupId: string, occId: string, label: string | null): Layout {
  const next = cloneLayout(layout);
  const group = findGroupById(next.tree, groupId);
  if (!group) return layout;
  const idx = group.tabs.findIndex((t) => t.occId === occId);
  if (idx === -1) return layout;
  const tabs = [...group.tabs];
  const trimmed = label?.trim();
  const t = tabs[idx];
  tabs[idx] = trimmed ? { ...t, label: trimmed } : (() => { const { label: _l, ...rest } = t; return rest; })();
  const updated: GroupNode = { ...group, tabs };
  return { ...next, tree: replaceNode(next.tree, groupId, updated) };
}

// ── Zoom ─────────────────────────────────────────────────────────────
// Zoom par niveau : layout.zoom (global), PanelOcc.zoom (panel OU mini-layout).
// L'effectif d'un niveau = produit des zooms des ancêtres (global × mini-layouts)
// × son propre zoom. Le scale est appliqué au corps de chaque panel (zoom CSS),
// jamais aux barres d'onglets.
//
// VERROU (cadenas) : un onglet locké ABSORBE son effectif total dans sa valeur
// propre (ex. 110% × 110% × 110% → 133%). Une fois locké, on AFFICHE cette valeur
// directement (non re-multipliée) : elle est FIXE, quoi qu'il arrive aux ancêtres.
// Au délock, on RECALCULE le propre = valeur lockée / ancêtres ACTUELS
// (ex. 133% ÷ (100% × 110%) = 121%). Les changements d'ancêtres n'affectent
// donc JAMAIS un panel locké.

/** Retourne le zoom propre d'un onglet (défaut 1×). */
export function occZoom(occ: PanelOcc): number {
  return occ.zoom?.value ?? 1;
}

/**
 * Effectif d'un onglet pour un facteur d'ancêtres donné.
 * Un onglet LOCKÉ affiche sa valeur propre DIRECTEMENT (le total absorbé au
 * lock) — les ancêtres ne le re-multiplient jamais (valeur figée).
 */
export function effectiveZoom(ancestorFactor: number, occ: PanelOcc): number {
  if (occ.zoom?.locked) return occZoom(occ);
  return ancestorFactor * occZoom(occ);
}

/** Trouve l'occurrence (et son groupe) par occId, y compris dans les sous-arbres. */
function findOccNode(tree: TreeNode, occId: string): { group: GroupNode; index: number; occ: PanelOcc } | null {
  if (tree.type === 'group') {
    const idx = tree.tabs.findIndex((t) => t.occId === occId);
    if (idx !== -1) return { group: tree, index: idx, occ: tree.tabs[idx] };
    for (const t of tree.tabs) {
      if (t.tree) {
        const r = findOccNode(t.tree, occId);
        if (r) return r;
      }
    }
    return null;
  }
  if (tree.type === 'split') {
    for (const c of tree.children) {
      const r = findOccNode(c, occId);
      if (r) return r;
    }
    return null;
  }
  return null;
}

/** Remplace l'occurrence `occId` par `replacement` (immutable, y compris sous-arbres). */
function replaceOcc(tree: TreeNode, occId: string, replacement: PanelOcc): TreeNode {
  if (tree.type === 'group') {
    const idx = tree.tabs.findIndex((t) => t.occId === occId);
    if (idx !== -1) {
      const tabs = [...tree.tabs];
      tabs[idx] = replacement;
      return { ...tree, tabs };
    }
    let changed = false;
    const tabs = tree.tabs.map((t) => {
      if (!t.tree) return t;
      const r = replaceOcc(t.tree, occId, replacement);
      if (r === t.tree) return t;
      changed = true;
      return { ...t, tree: r };
    });
    return changed ? { ...tree, tabs } : tree;
  }
  if (tree.type === 'split') {
    return { ...tree, children: tree.children.map((c) => replaceOcc(c, occId, replacement)) };
  }
  return tree;
}

/**
 * Fixe la valeur LOCALE du zoom d'un onglet (panel OU mini-layout), sans tenir
 * compte des ancêtres. Utilisé par la barre de zoom : +/− agissent sur la valeur
 * locale affichée (l'effectif = ancêtres × locale est calculé au rendu).
 * Un onglet LOCKÉ ignore les changements (valeur figée).
 */
export function setZoomLocal(layout: Layout, occId: string, value: number): Layout {
  const next = cloneLayout(layout);
  const found = findOccNode(next.tree, occId);
  if (!found) return layout;
  const { occ } = found;
  if (occ.zoom?.locked) return layout; // locké → figé
  const newVal = Math.max(1e-6, value);
  if (newVal === occZoom(occ)) return layout;
  return { ...next, tree: replaceOcc(next.tree, occId, { ...occ, zoom: { value: newVal, locked: false } }) };
}

/**
 * Change le zoom d'un onglet (panel OU mini-layout) pour viser un EFFECTIF
 * cible. `ancestorFactor` = produit des zooms des ancêtres (global × mini-layouts)
 * de cet onglet. Le zoom propre devient `effective / ancestorFactor`.
 * Un onglet LOCKÉ ignore les changements (valeur figée).
 */
export function setZoom(layout: Layout, occId: string, effective: number, ancestorFactor: number): Layout {
  const next = cloneLayout(layout);
  const found = findOccNode(next.tree, occId);
  if (!found) return layout;
  const { occ } = found;
  if (occ.zoom?.locked) return layout; // locké → figé
  const newVal = Math.max(1e-6, effective / Math.max(1e-6, ancestorFactor));
  if (newVal === occZoom(occ)) return layout;
  return { ...next, tree: replaceOcc(next.tree, occId, { ...occ, zoom: { value: newVal, locked: false } }) };
}

/**
 * Verrouille / déverrouille le zoom d'un onglet.
 * LOCK  : ABSORBE l'effectif total (ancestorFactor × propre) dans la valeur —
 *         elle devient FIXE, les ancêtres ne la re-multiplient plus.
 * UNLOCK : RECALCULE propre = valeur lockée ÷ ancêtres ACTUELS
 *         (ex. 133% ÷ (100% × 110%) = 121%).
 */
export function toggleZoomLock(layout: Layout, occId: string, ancestorFactor: number): Layout {
  const next = cloneLayout(layout);
  const found = findOccNode(next.tree, occId);
  if (!found) return layout;
  const { occ } = found;
  const locked = !(occ.zoom?.locked ?? false);
  const value = locked
    ? ancestorFactor * occZoom(occ) // LOCK : total absorbé
    : (occ.zoom?.value ?? 1) / Math.max(1e-6, ancestorFactor); // UNLOCK : ÷ parents
  const replacement: PanelOcc = { ...occ, zoom: { value: Math.max(1e-6, value), locked } };
  return { ...next, tree: replaceOcc(next.tree, occId, replacement) };
}

/** Zoom GLOBAL : fixe l'effectif global. Un global LOCKÉ ignore les changements. */
export function setGlobalZoom(layout: Layout, effective: number): Layout {
  if (layout.zoom?.locked) return layout; // global locké → figé
  const next = cloneLayout(layout);
  const newVal = Math.max(1e-6, effective);
  if (newVal === (layout.zoom?.value ?? 1)) return layout;
  next.zoom = { value: newVal, locked: layout.zoom?.locked ?? false };
  return next;
}

/** Verrouille / déverrouille le zoom global (pas d'ancêtres → valeur inchangée). */
export function toggleGlobalZoomLock(layout: Layout): Layout {
  const next = cloneLayout(layout);
  const locked = !(layout.zoom?.locked ?? false);
  next.zoom = { value: layout.zoom?.value ?? 1, locked };
  return next;
}

/** Reset du zoom d'un onglet (1×, délocké). */
export function resetZoom(layout: Layout, occId: string): Layout {
  const next = cloneLayout(layout);
  const found = findOccNode(next.tree, occId);
  if (!found) return layout;
  return { ...next, tree: replaceOcc(next.tree, occId, { ...found.occ, zoom: undefined }) };
}

/** Reset du zoom global (1×, délocké). */
export function resetGlobalZoom(layout: Layout): Layout {
  const next = cloneLayout(layout);
  next.zoom = undefined;
  return next;
}

/** Onglet actif d'un groupe (PanelOcc ou null). */
export function getGroupActive(layout: Layout, groupId: string): PanelOcc | null {
  const g = findGroupById(layout.tree, groupId);
  if (!g) return null;
  return g.tabs.find((t) => t.occId === g.active) || g.tabs[0] || null;
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
    // mini-layouts : si la cible est dans un sous-arbre d'onglet, insérer là
    const targetId = (target as GroupNode).id;
    for (let i = 0; i < tree.tabs.length; i++) {
      const t = tree.tabs[i];
      if (!t.tree || !targetId) continue;
      if (!findGroupById(t.tree, targetId)) continue;
      const tabs = [...tree.tabs];
      tabs[i] = { ...t, tree: insertSibling(t.tree, target, newGroup, direction) };
      return { ...tree, tabs };
    }
    return makeSplit(direction, [tree, newGroup]);
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

// Largeur fixe du séparateur (rendu SplitTree) — les sizes en % s'appliquent à
// la zone utilisable (total − largeurs des séparateurs), sinon la conversion
// px↔% dérive (un séparateur de 4px fixe fausse le ratio).
const SPLIT_SEPARATOR_PX = 4;

/**
 * Redimensionne un split : la position du séparateur (px, relative au container)
 * est convertie en % de la ZONE UTILISABLE (total − largeurs de séparateurs).
 * → le séparateur est exactement sous la souris, sans dérive ni compression flex.
 * @param sepPosPx position du CENTRE du séparateur relative au container (px)
 * @param totalPx  taille totale du split (px, incluant les séparateurs)
 */
export function resizeSplit(layout: Layout, splitId: string, index: number, sepPosPx: number, totalPx: number): Layout {
  const next = cloneLayout(layout);
  const split = findSplitById(next.tree, splitId);
  if (!split) return layout;
  const n = split.children.length;
  if (index < 0 || index >= n - 1) return layout;
  if (!(totalPx > 0)) return layout;

  let sizes = split.sizes && split.sizes.length === n ? [...split.sizes] : Array(n).fill(100 / n);
  // Zone utilisable (hors séparateurs) ; le panneau `index` va de 0 à sepPos−½sep.
  const sepW = SPLIT_SEPARATOR_PX;
  const usable = Math.max(1, totalPx - (n - 1) * sepW);
  const pct = Math.max(5, Math.min((sepPosPx - sepW / 2) / usable * 100, 95));
  // On fixe l'enfant index et on reporte le delta sur index+1 (somme des % = 100).
  const delta = pct - sizes[index];
  sizes[index] = pct;
  sizes[index + 1] = Math.max(5, sizes[index + 1] - delta);

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
  if (tree.type === 'group') {
    for (const t of tree.tabs) {
      if (t.tree) {
        const r = findSplitById(t.tree, splitId);
        if (r) return r;
      }
    }
    return null;
  }
  return null;
}

/** Remplace un nœud (split) par id — variante pour les splits. */
function replaceNodeById(tree: TreeNode, id: string, replacement: TreeNode): TreeNode {
  if (tree.type === 'split') {
    if (tree.id === id) return replacement;
    return { ...tree, children: tree.children.map((c) => replaceNodeById(c, id, replacement)) };
  }
  if (tree.type === 'group') {
    let changed = false;
    const tabs = tree.tabs.map((t) => {
      if (!t.tree) return t;
      const r = replaceNodeById(t.tree, id, replacement);
      if (r === t.tree) return t;
      changed = true;
      return { ...t, tree: r };
    });
    return changed ? { ...tree, tabs } : tree;
  }
  return tree;
}

/**
 * Ajoute un mini-layout : un ONGLET avec un sous-arbre interne (PanelOcc.tree).
 * C'est le concept "fake-window" : un panneau comme les autres, mais qui rend un
 * layout complet (splits + groupes) au lieu d'un composant. Plusieurs mini-layouts
 * = plusieurs onglets dans un groupe ; drag/drop/reorder/cross-group natifs.
 */
export function addMiniLayout(layout: Layout, groupId: string, panelId: string, params?: Record<string, any>): Layout {
  const next = cloneLayout(layout);
  const inner = makeGroup([]); // conteneur vide : on y déposera des panels
  const occ: PanelOcc = {
    panel: '__mini__',
    occId: genId('occ'),
    params,
    tree: inner,
  };
  const group = groupId ? findGroupById(next.tree, groupId) : null;
  if (!group) {
    // pas de groupe cible → on crée un groupe racine avec cet onglet
    return { ...next, tree: makeGroup([occ], occ.occId) };
  }
  // on pousse l'onglet mini-layout dans le groupe cible
  const updated: GroupNode = { ...group, tabs: [...group.tabs, occ], active: occ.occId };
  return { ...next, tree: replaceNode(next.tree, group.id, updated) };
}

/**
 * Supprime un mini-layout (onglet avec tree) d'un groupe.
 */
export function closeMiniLayout(layout: Layout, miniLayoutId: string): Layout {
  const next = cloneLayout(layout);
  const tree = removeMiniLayout(next.tree, miniLayoutId);
  if (!tree) return { ...next, tree: makeGroup([], genId()) };
  return { ...next, tree };
}

function removeMiniLayout(tree: TreeNode, miniLayoutId: string): TreeNode | null {
  if (tree.type === 'group') {
    // mini-layout = onglet `__mini__` : on retire l'onglet si son occId matche.
    const tabs = tree.tabs.filter((t) => !(t.panel === '__mini__' && t.occId === miniLayoutId));
    if (tabs.length === tree.tabs.length) return tree;
    if (tabs.length === 0) return null;
    const active = tree.tabs.some((t) => t.occId === tree.active) && tabs.some((t) => t.occId === tree.active)
      ? tree.active : tabs[0].occId;
    return { ...tree, tabs, active };
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

/**
 * Applique `fn` au sous-arbre d'un onglet mini-layout (`__mini__`) repéré par
 * son occId. Retourne un NOUVEAU layout (les autres onglets restent intacts).
 * Sert à faire fonctionner les mutations (activateTab, closeTab, moveTab,
 * splitGroup, resizeSplit…) SUR le tree interne d'un mini-layout donné.
 */
export function mapMiniLayout(layout: Layout, miniOccId: string, fn: (tree: TreeNode) => TreeNode): Layout {
  const next = cloneLayout(layout);
  return { ...next, tree: mapMiniNode(next.tree, miniOccId, fn) };
}

function mapMiniNode(tree: TreeNode, miniOccId: string, fn: (t: TreeNode) => TreeNode): TreeNode {
  if (tree.type === 'group') {
    let changed = false;
    const tabs = tree.tabs.map((t) => {
      if (t.tree && t.occId === miniOccId) {
        changed = true;
        return { ...t, tree: fn(t.tree) };
      }
      if (t.tree) {
        const nt = mapMiniNode(t.tree, miniOccId, fn);
        if (nt !== t.tree) { changed = true; return { ...t, tree: nt }; }
      }
      return t;
    });
    return changed ? { ...tree, tabs } : tree;
  }
  if (tree.type === 'split') {
    let changed = false;
    const children = tree.children.map((c) => {
      const nc = mapMiniNode(c, miniOccId, fn);
      if (nc !== c) changed = true;
      return nc;
    });
    return changed ? { ...tree, children } : tree;
  }
  return tree;
}

// ── Invariants (validation) ──────────────────────────────────────────

/** Vérifie les invariants du layout. Retourne la liste des violations. */
export function validateLayout(layout: Layout): string[] {
  const errors: string[] = [];
  const seenOcc = new Set<string>();

  function walk(tree: TreeNode, path: string, inMini = false) {
    if (tree.type === 'group') {
      // Le groupe interne d'un mini-layout peut être vide (conteneur : on y
      // dépose des panels ensuite) — ce n'est pas une violation d'invariant.
      if (tree.tabs.length === 0 && !inMini) errors.push(`${path}: groupe vide (invariant: ≥1 onglet)`);
      if (!tree.tabs.some((t) => t.occId === tree.active)) {
        if (tree.tabs.length > 0) errors.push(`${path}: active '${tree.active}' introuvable`);
      }
      for (const t of tree.tabs) {
        if (seenOcc.has(t.occId)) errors.push(`${path}: occId dupliqué '${t.occId}'`);
        seenOcc.add(t.occId);
        if (t.tree) walk(t.tree, `${path}.mini[${t.panel}]`, true);
      }
    } else {
      if (tree.children.length === 0) errors.push(`${path}: split vide`);
      for (let i = 0; i < tree.children.length; i++) walk(tree.children[i], `${path}[${i}]`, inMini);
    }
  }
  walk(layout.tree, 'root');
  return errors;
}
