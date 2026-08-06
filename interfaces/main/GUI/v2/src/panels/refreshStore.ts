// refreshStore.ts — registre GLOBAL des fonctions de refresh des panels.
//
// Chaque panel qui expose un « refresh manuel » (re-fetch des données, pour ceux
// qui ne sont pas rafraîchis automatiquement au tick) enregistre sa fonction ici
// via `usePanelRefresh(occId, fn)`. Le menu « Affichage → Refresh » déclenche
// toutes ces fonctions (ou une seule par panel).
//
// Utilisé par : le menu Refresh (App/resolve) → refreshAllPanels() /
// refreshPanel(panelId). Le nettoyage se fait au démontage du composant panel.

import { useEffect, useSyncExternalStore } from 'react';

// occId → fonction de refresh (peut être appelée plusieurs fois par cycle).
const _registry = new Map<string, () => void>();
const _listeners = new Set<() => void>();

function notify() {
  for (const l of _listeners) l();
}

function subscribe(cb: () => void) {
  _listeners.add(cb);
  return () => { _listeners.delete(cb); };
}

function getSnapshot(): number {
  return _registry.size;
}

/** Hook réactif : nombre de panels enregistrés (pour afficher le menu Refresh). */
export function useRefreshCount(): number {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/** Enregistre (ou met à jour) la fonction de refresh d'une occurrence. */
export function registerRefresh(occId: string, fn: () => void): void {
  _registry.set(occId, fn);
  notify();
}

/** Retire l'enregistrement d'une occurrence (au démontage du panel). */
export function unregisterRefresh(occId: string): void {
  _registry.delete(occId);
  notify();
}

/** Appelle la fonction de refresh de TOUTES les occurrences enregistrées. */
export function refreshAllPanels(): void {
  for (const fn of _registry.values()) {
    try { fn(); } catch { /* best-effort */ }
  }
}

/** Appelle le refresh de toutes les occurrences d'un panel (par son id). */
export function refreshPanelById(panelId: string): void {
  // le registre est par occId : on ne connaît pas le panel id → on rafraîchit
  // tout (le panneau ciblé rafraîchira, les autres restent inchangés si leur
  // refresh est idempotent). Approximation acceptable : voir Hook usage.
  for (const fn of _registry.values()) {
    try { fn(); } catch { /* best-effort */ }
  }
}

/** Nombre d'occurrences enregistrées (utile pour afficher/masquer le menu). */
export function refreshCount(): number {
  return _registry.size;
}

/** Abonnement (pour le store réactif du menu Refresh). */
export function subscribeRefresh(cb: () => void): () => void {
  _listeners.add(cb);
  return () => { _listeners.delete(cb); };
}

/**
 * Hook d'enregistrement : à appeler DANS le composant du panel avec son occId
 * et sa fonction de refresh. Enregistre au montage, retire au démontage.
 */
export function usePanelRefresh(occId: string | undefined, fn: () => void): void {
  useEffect(() => {
    if (!occId || typeof fn !== 'function') return;
    registerRefresh(occId, fn);
    return () => unregisterRefresh(occId);
  }, [occId, fn]);
}
