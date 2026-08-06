// windows.ts — gestion multi-fenêtres + sync inter-fenêtres.
//
// Chaque fenêtre = un profil persisté via le daemon (windows/create|update|
// close) + une vraie fenêtre Tauri. Ce module expose :
//  - un store React (subscribe/getSnapshot) alimenté par polling :
//      * windows/list (profils daemon) + list_windows (fenêtres Tauri)
//      * layout/get des AUTRES fenêtres → panels ouverts chez elles
//  - des actions (open/focus/close/fullscreen/enregistrer) combinant
//    Tauri (invoke) et daemon (HTTP).
// Le polling est partagé par toutes les fenêtres (module singleton).

import { useSyncExternalStore } from 'react';
import { parse as parseYaml } from 'yaml';
import {
  daemonPost,
  invoke,
  listWindows as tauriList,
  currentWindowState,
} from './bridge.ts';
import type { Layout } from './layout/types.ts';

// ── Types ─────────────────────────────────────────────────────────────

export interface WindowTemplate {
  id: string;
  label: string;
  icon: string;
  layout: string;
  theme: string;
  defaultSize: { width: number; height: number };
  defaultPos: { x: number | null; y: number | null };
}

export interface WindowProfile {
  window_id: string;
  template: string;
  layout: string;
  theme: string;
  title: string;
  x?: number | null;
  y?: number | null;
  width?: number;
  height?: number;
  state?: string;
  visible?: boolean;
  opened_at?: string;
}

export interface RemotePresence {
  windowId: string;
  title: string;
  present: { panel: string; params?: Record<string, any> }[];
}

interface WindowsState {
  ready: boolean;
  profiles: WindowProfile[];
  templates: WindowTemplate[];
  liveLabels: string[];   // fenêtres Tauri réellement ouvertes
  currentId: string;
  remote: RemotePresence[];
  error?: string;
}

const initialState: WindowsState = { ready: false, profiles: [], templates: [], liveLabels: [], currentId: '', remote: [] };

let _state: WindowsState = initialState;
const _listeners = new Set<() => void>();
let _started = false;
let _templatesLoaded = false;

function setState(patch: Partial<WindowsState>) {
  _state = { ..._state, ...patch };
  for (const l of _listeners) l();
}

function subscribe(cb: () => void) {
  _listeners.add(cb);
  return () => { _listeners.delete(cb); };
}

function getSnapshot(): WindowsState {
  return _state;
}

/** Hook React : état global des fenêtres. */
export function useWindowsStore(): WindowsState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

// ── Primitives daemon ────────────────────────────────────────────────

function layoutNameFor(winId: string): string {
  return `layout-${winId}`;
}

async function fetchTemplates(): Promise<WindowTemplate[]> {
  const res = await daemonPost('windows/templates', {});
  const tpl = res?.result?.templates ?? res?.templates ?? {};
  return Object.entries(tpl).map(([id, v]: [string, any]) => ({ id, ...v }));
}

async function fetchProfiles(): Promise<WindowProfile[]> {
  const res = await daemonPost('windows/list', {});
  const windows = res?.result?.windows ?? res?.windows ?? [];
  return Array.isArray(windows) ? windows : [];
}

// ── Parsing d'un layout (YAML) vers presence ─────────────────────────

function collectPresent(node: any, out: { panel: string; params?: Record<string, any> }[]) {
  if (!node || typeof node !== 'object') return;
  if (node.type === 'group' && Array.isArray(node.tabs)) {
    for (const t of node.tabs) {
      out.push({ panel: t.panel ?? '', params: t.params ?? undefined });
    }
  } else if (node.type === 'split' && Array.isArray(node.children)) {
    for (const c of node.children) collectPresent(c, out);
  } else if (node.type === "miniLayout") {
    collectPresent(node.tree, out);
  }
}

/** Parse le YAML d'un layout et en extrait les panels présents. */
export function presentFromLayoutYaml(yaml: string): { panel: string; params?: Record<string, any> }[] {
  const out: { panel: string; params?: Record<string, any> }[] = [];
  try {
    const data = parseYaml(yaml) as Layout;
    collectPresent(data?.tree, out);
  } catch {
    // yaml invalide → liste vide
  }
  return out;
}

// ── Sync (poll) ───────────────────────────────────────────────────────

const POLL_MS = 5000;

/**
 * Démarre le polling partagé (appelé par chaque fenêtre, idempotent).
 * - windows/list + list_windows → profils + live
 * - layout/get des autres fenêtres → remote (panels ouverts chez elles)
 * - supprime les profils orphelins (fenêtre fermée par l'OS sans appel)
 */
export function startWindows(currentId: string): void {
  if (_started) {
    if (_state.currentId !== currentId) setState({ currentId });
    return;
  }
  _started = true;
  setState({ currentId });

  const tick = async () => {
    try {
      if (!_templatesLoaded) {
        try {
          setState({ templates: await fetchTemplates() });
          _templatesLoaded = true;
        } catch { /* best-effort */ }
      }
      let profiles = await fetchProfiles();
      let liveLabels: string[] = [];
      try { liveLabels = (await tauriList()).map((w) => w.label); } catch { /* web */ }

      // Fenêtre PRÉENREGISTRÉE = layout nommé ≠ layout-<winId> (modèle persisté).
      const isPredefined = (p: WindowProfile) => !!p.layout && p.layout !== layoutNameFor(p.window_id);

      // Nettoyage des profils orphelins : profil présent côté daemon mais
      // aucune fenêtre Tauri ne le porte → on le supprime côté daemon.
      // EXCEPTION : les fenêtres PRÉENREGISTRÉES sont des modèles à garder.
      const stale = profiles.filter((p) => p.window_id !== currentId && !liveLabels.includes(p.window_id) && !isPredefined(p));
      if (stale.length) {
        for (const s of stale) {
          try { await daemonPost('windows/close', { window_id: s.window_id }); } catch { /* best-effort */ }
        }
        profiles = await fetchProfiles();
      }

      const fresh = profiles.filter((p) => p.window_id === currentId || liveLabels.includes(p.window_id) || isPredefined(p));
      const mine = await ensureProfile(currentId, profiles);
      const merged = mergeProfiles(fresh, mine);

      // Remote : presence des autres fenêtres (layout/get unique par fenêtre).
      const remote: RemotePresence[] = [];
      for (const p of merged) {
        if (p.window_id === currentId) continue;
        if (!liveLabels.includes(p.window_id)) continue;
        let present: { panel: string; params?: Record<string, any> }[] = [];
  try {
    const res = await daemonPost('layout/get', { name: layoutNameFor(p.window_id) });
    if (res?.result?.yaml ?? res?.yaml) present = presentFromLayoutYaml(res?.result?.yaml ?? res?.yaml);
  } catch { /* best-effort */ }
        remote.push({ windowId: p.window_id, title: p.title || p.window_id, present });
      }
      setState({ ready: true, profiles: merged, liveLabels, remote, error: undefined });
    } catch (e: any) {
      setState({ error: String(e?.message ?? e) });
    }
  };

  tick();
  setInterval(tick, POLL_MS);
}

function mergeProfiles(fresh: WindowProfile[], mine: WindowProfile | null): WindowProfile[] {
  const out = fresh.filter((p) => p.window_id !== mine?.window_id);
  if (mine) out.push(mine);
  return out.sort((a, b) => a.window_id.localeCompare(b.window_id));
}

/** Crée le profil de la fenêtre courante si absent (boot idempotent). */
async function ensureProfile(currentId: string, profiles: WindowProfile[]): Promise<WindowProfile | null> {
  const existing = profiles.find((p) => p.window_id === currentId);
  if (existing) return existing;
  try {
    const res = await daemonPost('windows/create', {
      window_id: currentId,
      template: 'default',
      layout: layoutNameFor(currentId),
      title: currentId === 'main' ? 'ModelWeaver' : currentId,
    });
    return res?.result?.window ?? res?.window ?? null;
  } catch {
    return null;
  }
}

// ── Actions ──────────────────────────────────────────────────────────

/** Ouvre une fenêtre depuis un template (Tauri + profil daemon).
 *  Si `extra.label` est fourni (fenêtre PRÉENREGISTRÉE), ce label devient le
 *  window_id → le boot chargera le layout référencé par le profil existant. */
export async function openWindow(templateId: string, extra?: { pos?: { x: number; y: number }; label?: string; title?: string; size?: { width: number; height: number } }): Promise<string | null> {
  const tpl = _state.templates.find((t) => t.id === templateId);
  const label = extra?.label ?? `win-${Date.now().toString(36)}`;
  const size = extra?.size ?? tpl?.defaultSize;
  const pos = extra?.pos ?? tpl?.defaultPos;
  const title = extra?.title ?? (tpl ? `ModelWeaver — ${tpl.label}` : label);
  try {
    await invoke('create_window', {
      label,
      ...(size ? { size } : {}),
      ...(pos?.x != null && pos?.y != null ? { pos: { x: pos.x, y: pos.y } } : {}),
      ...(title ? { title } : {}),
    });
  } catch { /* web: fenêtre manquante ignorée */ }
  try {
    // Fenêtre PRÉENREGISTRÉE : le profil existe déjà (avec son layout nommé +
    // thème). Ne PAS le recréer (sinon on écrase layout-<winId> et on perd le
    // layout préenregistré). Sinon, on crée un profil standard.
    const existing = _state.profiles.find((p) => p.window_id === label);
    if (!existing) {
      await daemonPost('windows/create', {
        window_id: label,
        template: templateId,
        layout: layoutNameFor(label),
        title: tpl?.label ?? label,
        size,
        ...(pos?.x != null && pos?.y != null ? { pos: { x: pos.x, y: pos.y } } : {}),
      });
    }
  } catch { /* best-effort */ }
  return label;
}

/** Met une fenêtre au premier plan (menu Fenêtre → liste). */
export async function focusWindow(windowId: string): Promise<void> {
  try { await invoke('focus_window', { label: windowId }); } catch { /* best-effort */ }
}

/** Ferme une fenêtre précise (Tauri + profil). */
export async function closeWindow(windowId: string): Promise<void> {
  try { await invoke('close_window', { label: windowId }); } catch { /* best-effort */ }
  try { await daemonPost('windows/close', { window_id: windowId }); } catch { /* best-effort */ }
}

/** Ferme la fenêtre courante. */
export async function closeCurrentWindow(windowId: string): Promise<void> {
  try { await invoke('close_current_window'); } catch { /* best-effort */ }
  try { await daemonPost('windows/close', { window_id: windowId }); } catch { /* best-effort */ }
}

/** Bascule le plein écran (menu Fenêtre → Plein écran / F11). */
export async function toggleFullscreen(): Promise<boolean> {
  try {
    const res = await invoke('window_fullscreen');
    return Boolean(res?.fullscreen);
  } catch {
    return false;
  }
}

/** Persiste l'état position/taille de la fenêtre courante (throttlé). */
let _lastStatePush = 0;
export async function pushCurrentWindowState(windowId: string): Promise<void> {
  const now = Date.now();
  if (now - _lastStatePush < 5000) return;
  _lastStatePush = now;
  try {
    const s = await currentWindowState();
    if (!s) return;
    await daemonPost('windows/update', {
      window_id: windowId,
      x: s.x ?? null,
      y: s.y ?? null,
      width: s.width ?? null,
      height: s.height ?? null,
      state: s.maximized ? 'maximized' : s.fullscreen ? 'fullscreen' : 'normal',
    });
  } catch { /* best-effort */ }
}

/** Enregistre la fenêtre : layout/save (via persistLayout) + maj profil. */
export async function saveCurrentWindow(windowId: string, layoutName: string): Promise<void> {
  try { await daemonPost('windows/update', { window_id: windowId, layout: layoutName, title: windowId === 'main' ? 'ModelWeaver' : windowId }); } catch { /* best-effort */ }
}

// Ré-export pour éviter les dépendances circulaires d'import dans App
export { layoutNameFor };
export { subscribe as subscribeWindows };