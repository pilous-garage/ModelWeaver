// dragStore.ts — état global du drag & drop d'onglets.
//
// Coordination CROSS-GROUP : pendant un drag, chaque TabGroup enregistre son
// rect + une fonction de calcul d'index dans un registre partagé. Le groupe
// qui a démarré le drag détermine la cible (groupe survolé) + la zone, et
// notifie tous les groupes via un store React (marqueurs partout).
//
// Zones (pour le groupe ciblé) :
//   - barre d'onglets → 'bar' (reorder / déplacement, à l'index du curseur)
//   - centre du corps → 'center' (ajout en fin de file du groupe cible)
//   - bords (10%)      → 'left'|'right'|'top'|'bottom' (split du groupe cible)

import { useSyncExternalStore } from 'react';

export type DropZone = 'bar' | 'center' | 'left' | 'right' | 'top' | 'bottom' | 'outside';

export interface GroupHandle {
  id: string;
  rect: () => DOMRect;
  barRect: () => DOMRect;
  /** index d'insertion dans la barre du groupe pour une abscisse donnée. */
  computeIndex: (x: number, excludeOccId: string) => number;
}

export interface DropState {
  targetGroupId: string | null;
  zone: DropZone | null;
  insertIndex: number | null;
  excludeOccId: string;
}

const _groups: GroupHandle[] = [];
let _drop: DropState = { targetGroupId: null, zone: null, insertIndex: null, excludeOccId: '' };
const _listeners = new Set<() => void>();

function notify() {
  for (const l of _listeners) l();
}

/** Enregistre un groupe (rect + calcul d'index). Retourne un détach. */
export function registerGroup(g: GroupHandle): () => void {
  _groups.push(g);
  return () => {
    const i = _groups.indexOf(g);
    if (i >= 0) _groups.splice(i, 1);
  };
}

export function setDrop(d: DropState): void { _drop = d; notify(); }
export function clearDrop(): void { _drop = { targetGroupId: null, zone: null, insertIndex: null, excludeOccId: '' }; notify(); }
export function getDrop(): DropState { return _drop; }

function subscribe(cb: () => void) {
  _listeners.add(cb);
  return () => { _listeners.delete(cb); };
}
function getSnapshot(): DropState { return _drop; }

/** Hook React : état de drop courant (tous les groupes s'y abonnent). */
export function useDropState(): DropState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

const EDGE = 0.10;

const ZERO = { x: 0, y: 0, width: 0, height: 0, left: 0, top: 0, right: 0, bottom: 0 } as DOMRect;

/**
 * Détermine la cible du drop pour un point (x,y) :
 * le groupe au plus petit rect contenant le point (le plus précis, gère les
 * groupes imbriqués des mini-layouts), puis la zone dans ce groupe.
 */
export function computeDrop(x: number, y: number, excludeOccId: string): DropState {
  const best = groupAt(x, y);
  if (!best) return { targetGroupId: null, zone: 'outside', insertIndex: null, excludeOccId };
  const r = best.rect();
  const br = best.barRect();
  // barre d'onglets → replacement
  if (br.width > 0 && x >= br.left && x <= br.right && y >= br.top && y <= br.bottom) {
    return { targetGroupId: best.id, zone: 'bar', insertIndex: best.computeIndex(x, excludeOccId), excludeOccId };
  }
  // bords (10%) → split
  const relX = (x - r.left) / r.width;
  const relY = (y - r.top) / r.height;
  if (relX < EDGE) return { targetGroupId: best.id, zone: 'left', insertIndex: null, excludeOccId };
  if (relX > 1 - EDGE) return { targetGroupId: best.id, zone: 'right', insertIndex: null, excludeOccId };
  if (relY < EDGE) return { targetGroupId: best.id, zone: 'top', insertIndex: null, excludeOccId };
  if (relY > 1 - EDGE) return { targetGroupId: best.id, zone: 'bottom', insertIndex: null, excludeOccId };
  // centre → ajout en fin de file
  return { targetGroupId: best.id, zone: 'center', insertIndex: null, excludeOccId };
}

/**
 * Groupe le plus PRÉCIS contenant un point (le plus petit rect). Gère les
 * groupes imbriqués (mini-layouts) : retourne le plus imbriqué. Utilisé pour
 * le zoom clavier (Ctrl+±/Ctrl+0) : le zoom s'applique au panel sous le curseur.
 */
export function groupAt(x: number, y: number): GroupHandle | null {
  let best: GroupHandle | null = null;
  let bestArea = Infinity;
  for (const g of _groups) {
    const r = g.rect() || ZERO;
    if (r.width === 0) continue;
    if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
      const area = r.width * r.height;
      if (area < bestArea) { best = g; bestArea = area; }
    }
  }
  return best;
}
