// windowStore — état partagé des fenêtres ouvertes (synchro inter-fenêtres).
//
// Chaque fenêtre Tauri est une Webview indépendante (états React séparés).
// Pour que le menu Fenêtre/Panneaux connaisse les autres fenêtres, chaque
// fenêtre publie son état (label → layout/thème/titre + panels du panelTree)
// via broadcast_window_state (événement Tauri) ET via localStorage (fallback
// navigateur). Les autres fenêtres écoutent et mettent à jour leur copie.
//
// L'événement Tauri est la source fiable entre Webviews Tauri ; localStorage
// couvre le mode navigateur (une seule fenêtre).

import { invoke, daemonPost } from './bridge.ts';

export interface WindowState {
  label: string;
  layout: string;
  theme: string;
  title: string;
  panels: string[];   // ids des panels visibles dans le panelTree
}

const LS_KEY = 'mw:windows-state';
type Listener = (windows: WindowState[]) => void;

let _windows: WindowState[] = [];
const _listeners = new Set<Listener>();

function readLocal(): WindowState[] {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || '[]');
  } catch { return []; }
}

function writeLocal(ws: WindowState[]) {
  try { localStorage.setItem(LS_KEY, JSON.stringify(ws)); } catch {}
}

function notify() {
  for (const fn of _listeners) fn(_windows);
}

export function subscribeWindows(fn: Listener): () => void {
  _listeners.add(fn);
  fn(_windows);
  return () => _listeners.delete(fn);
}

export function getWindows(): WindowState[] { return _windows; }

export function getWindowState(label: string): WindowState | undefined {
  return _windows.find((w) => w.label === label);
}

/** Met à jour l'état local + broadcast Tauri + localStorage. */
export async function publishWindowState(state: WindowState): Promise<void> {
  const prev = _windows;
  const idx = _windows.findIndex((w) => w.label === state.label);
  const next = [..._windows];
  if (idx >= 0) next[idx] = state; else next.push(state);
  _windows = next;
  writeLocal(next);
  notify();
  try {
    await invoke('broadcast_window_state', {
      label: state.label, layout: state.layout, theme: state.theme, title: state.title,
    });
  } catch {}
  // Fusionne ce que les autres fenêtres ont broadcasté (peut arriver plus tard).
  const remote = readLocal();
  if (remote.length > 0) {
    for (const r of remote) {
      if (r.label === state.label) continue;
      const ri = _windows.findIndex((w) => w.label === r.label);
      const merged: WindowState[] = [..._windows];
      if (ri >= 0) merged[ri] = { ...merged[ri], layout: r.layout, theme: r.theme, title: r.title };
      else merged.push(r);
      _windows = merged;
    }
    writeLocal(_windows);
    notify();
  }
}

/** Publie l'état courant d'une fenêtre à partir de son layout/thème. */
export async function publishLayout(label: string, layout: string, theme: string, title: string, panels: string[]): Promise<void> {
  await publishWindowState({ label, layout, theme, title, panels });
}

/** Retire une fenêtre de l'état partagé (fermée). */
export function forgetWindow(label: string) {
  _windows = _windows.filter((w) => w.label !== label);
  writeLocal(_windows);
  notify();
}

/** S'abonne aux événements Tauri de broadcast venant des autres fenêtres. */
export async function startWindowSync(): Promise<void> {
  const hasTauri = !!(window as any).__TAURI_INTERNALS__;
  if (hasTauri) {
    try {
      const { listen } = await import('@tauri-apps/api/event');
      await listen('mw:window-state', (event: any) => {
        const payload = event?.payload;
        if (!payload?.label) return;
        const self = (window as any).__MW_WINDOW_LABEL;
        if (payload.label === self) return;  // son propre broadcast
        const idx = _windows.findIndex((w) => w.label === payload.label);
        const next = [..._windows];
        if (idx >= 0) next[idx] = { ...next[idx], layout: payload.layout, theme: payload.theme, title: payload.title };
        else next.push({ label: payload.label, layout: payload.layout, theme: payload.theme, title: payload.title, panels: [] });
        _windows = next;
        writeLocal(_windows);
        notify();
      });
    } catch {}
  }
  // Chargement initial depuis le localStorage (fenêtres précédentes connues).
  const local = readLocal();
  if (local.length > 0) {
    for (const l of local) {
      if (!_windows.find((w) => w.label === l.label)) _windows.push(l);
    }
    notify();
  }
}

/** Chargement initial depuis le backend (fenêtres persistées). */
export async function loadBackendWindows(): Promise<WindowState[]> {
  try {
    const res = await daemonPost('windows/list', {});
    const ws = res?.result?.windows || res?.windows || [];
    for (const w of ws) {
      const label = w.window_id;
      const existing = _windows.find((x) => x.label === label);
      if (existing) {
        existing.layout = w.layout || existing.layout;
        existing.theme = w.theme || existing.theme;
        existing.title = w.title || existing.title;
      } else {
        _windows.push({ label, layout: w.layout || label, theme: w.theme || 'dark', title: w.title || label, panels: [] });
      }
    }
    writeLocal(_windows);
    notify();
  } catch {}
  return _windows;
}

/** Extracteur : ids des panels visibles d'un panelTree (supporte les groupes
 * d'onglets : tabs du groupe). */
export function collectTreePanels(tree: any): string[] {
  const out: string[] = [];
  const walk = (n: any) => {
    if (!n) return;
    if (n.type === 'group') {
      const tabs = (n.tabs || []).filter((t: string) => t);
      for (const t of tabs) if (t) out.push(t);
    } else if (n.type === 'panel' && n.id && n.visible !== false) {
      out.push(n.id);
    }
    if (n.children) for (const c of n.children) walk(c);
  };
  walk(tree);
  return out;
}
