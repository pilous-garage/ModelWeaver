// Persistance des layouts et sessions en YAML (.layout.yaml, .session.yaml).
//
// Le layout est la source de vérité : à chaque mutation (ops.ts), on le
// sauvegarde en temps réel via le daemon (routes layout/save, session/save).

import { parse, stringify } from 'yaml';
import type { Layout, Session } from './types.ts';

// ── Sérialisation YAML ───────────────────────────────────────────────

/** Sérialise un layout en YAML. */
export function layoutToYaml(layout: Layout): string {
  return stringify(layout, { indent: 2 });
}

/** Parse un layout depuis du YAML (avec normalisation des types). */
export function layoutFromYaml(yaml: string): Layout {
  const data = parse(yaml) as Layout;
  return normalizeLayout(data);
}

/** Sérialise une session en YAML. */
export function sessionToYaml(session: Session): string {
  return stringify(session, { indent: 2 });
}

/** Parse une session depuis du YAML. */
export function sessionFromYaml(yaml: string): Session {
  const data = parse(yaml) as Session;
  if (!data.windows) data.windows = [];
  return data;
}

// ── Normalisation ────────────────────────────────────────────────────

/**
 * Normalise un layout chargé depuis YAML/JSON : garantit les types des nœuds,
 * les occId uniques, et l'invariant "tout panel dans un onglet".
 * Les nœuds sans `type` sont inférés (direction → split, tabs → group, tree → miniLayout).
 */
export function normalizeLayout(layout: Layout): Layout {
  if (!layout.tree) {
    layout.tree = { type: 'group', id: 'pg-root', tabs: [], active: '' };
  }
  layout.tree = normalizeNode(layout.tree);
  return layout;
}

function normalizeNode(node: any): any {
  if (!node || typeof node !== 'object') {
    return { type: 'group', id: 'pg-auto', tabs: [], active: '' };
  }
  // Inférence du type si absent
  let type = node.type;
  if (!type) {
    if (node.direction && Array.isArray(node.children)) type = 'split';
    else if (Array.isArray(node.tabs)) type = 'group';
    else if (node.tree) type = 'miniLayout';
    else type = 'group';
  }
  if (type === 'split') {
    const children = (node.children || []).map(normalizeNode);
    return {
      type: 'split',
      id: node.id || `sp-${Math.random().toString(36).slice(2, 7)}`,
      direction: node.direction === 'vertical' ? 'vertical' : 'horizontal',
      children,
      sizes: node.sizes,
    };
  }
  // mini-layout (ancien nom "slip" accepté en entrée pour rétro-compat)
  if (type === 'miniLayout' || type === 'slip') {
    return { type: 'miniLayout', id: node.id || `mini-${Math.random().toString(36).slice(2, 7)}`, title: node.title, tree: normalizeNode(node.tree), menuExtra: node.menuExtra };
  }
  // group
  const tabs = (node.tabs || []).map((t: any) => ({
    panel: t.panel ?? t.id ?? '',
    occId: t.occId ?? t.id ?? `occ-${Math.random().toString(36).slice(2, 7)}`,
    ...(t.params ? { params: t.params } : {}),
    ...(t.theme ? { theme: t.theme } : {}),
  }));
  const active = (node.active && tabs.some((t: any) => t.occId === node.active))
    ? node.active
    : (tabs[0]?.occId ?? '');
  return { type: 'group', id: node.id || `pg-${Math.random().toString(36).slice(2, 7)}`, tabs, active, ...(node.hideTabs ? { hideTabs: true } : {}) };
}

// ── Transport daemon (best-effort) ───────────────────────────────────

// Debounce : les mutations continues (drag d'onglet, resize de séparateur)
// déclenchent persistLayout à chaque mousemove. On écrit au plus une fois par
// fenêtre de temps, toujours avec le DERNIER layout (évite N requêtes et les
// courses d'écriture).
const _pendingLayout: { id: string; yaml: string; post?: (route: string, body: any) => Promise<any> }[] = [];
let _layoutTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleLayoutFlush() {
  if (_layoutTimer) return;
  _layoutTimer = setTimeout(() => {
    _layoutTimer = null;
    const items = _pendingLayout.splice(0, _pendingLayout.length);
    // écrit le dernier item de chaque id (les intermédiaires sont superflus)
    const lastByKey = new Map<string, typeof items[number]>();
    for (const it of items) lastByKey.set(it.id, it);
    for (const it of lastByKey.values()) {
      it.post?.('layout/save', { name: it.id, yaml: it.yaml }).catch(() => {});
    }
  }, 250);
}

/**
 * Sauvegarde temps réel du layout via le daemon (layout/save).
 * Appelée après chaque mutation. Best-effort (ne casse jamais), debounce 250ms.
 */
export async function persistLayout(layout: Layout, opts?: { post?: (route: string, body: any) => Promise<any> }): Promise<void> {
  const post = opts?.post;
  if (!post) return;
  _pendingLayout.push({ id: layout.id, yaml: layoutToYaml(layout), post });
  scheduleLayoutFlush();
}

/** Sauvegarde une session via le daemon. */
export async function persistSession(session: Session, opts?: { post?: (route: string, body: any) => Promise<any> }): Promise<void> {
  const post = opts?.post;
  if (!post) return;
  try {
    await post('session/save', { name: session.id, yaml: sessionToYaml(session) });
  } catch {
    // best-effort
  }
}
