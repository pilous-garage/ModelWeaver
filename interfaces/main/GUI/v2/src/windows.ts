// windows.ts — gestion multi-fenêtres + sync inter-fenêtres.
//
// Chaque fenêtre = un profil persisté via le daemon + une vraie fenêtre Tauri.
// Depuis la refonte « sessions », le daemon expose un store structuré
// (windows-store/* + win-session/*) avec 3 listes de fenêtres :
//   - officielles (templates intégrés au produit)
//   - enregistrées (profils « modèle », hors session, register/)
//   - vivantes (fenêtres réellement ouvertes de la session active)
// Ce module expose :
//  - un store React (subscribe/getSnapshot) alimenté par polling :
//      * windows/list + list_windows (profils daemon + fenêtres Tauri)
//      * windows-store/state + windows-store/windows-list (sessions + 3 listes)
//      * layout/get des AUTRES fenêtres → panels ouverts chez elles
//  - des actions (open/focus/close/fullscreen/enregistrer/sessions) combinant
//    Tauri (invoke) et daemon (HTTP).
// Le polling est partagé par toutes les fenêtres (module singleton).

import { useSyncExternalStore } from 'react';
import { parse as parseYaml } from 'yaml';
import {
  daemonPost,
  invoke,
  createWindow,
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

/** Source d'une fenêtre dans le store windows-store. */
export type WindowSource = 'official' | 'registered' | 'live';

/** Fenêtre ENREGISTRÉE (profil « modèle » conservé hors session). */
export interface RegisteredWindow {
  window_id: string;
  title: string;
  layout?: string;
  theme?: string;
  x?: number | null;
  y?: number | null;
  width?: number;
  height?: number;
  state?: string;
}

/** Fenêtre OFFICIELLE (template intégré au produit). */
export interface OfficialWindow {
  id: string;
  title: string;
  layout: string;
  theme: string;
  width?: number | null;
  height?: number | null;
}

/** Session de fenêtres (métadonnées côté daemon). */
export interface SessionInfo {
  id: string;
  name: string;
  theme?: string | null;
  open_windows: string[];
}

interface WindowsState {
  ready: boolean;
  profiles: WindowProfile[];
  templates: WindowTemplate[];
  liveLabels: string[];   // fenêtres Tauri réellement ouvertes
  currentId: string;
  remote: RemotePresence[];
  sessions: SessionInfo[];
  activeSession: SessionInfo | null;
  official: OfficialWindow[];
  registered: WindowProfile[];
  highlightWindow?: string | null;
  error?: string;
}

const initialState: WindowsState = {
  ready: false,
  profiles: [],
  templates: [],
  liveLabels: [],
  currentId: '',
  remote: [],
  sessions: [],
  activeSession: null,
  official: [],
  registered: [],
  highlightWindow: null,
};

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
      // mini-layout : parcourir le sous-arbre de l'onglet
      if (t.tree) collectPresent(t.tree, out);
    }
  } else if (node.type === 'split' && Array.isArray(node.children)) {
    for (const c of node.children) collectPresent(c, out);
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
 * - windows-store/state + windows-store/windows-list → sessions + 3 listes
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

      // Store structuré (sessions + 3 listes) : best-effort, indépendant du
      // reste du tick (ne doit jamais faire planter le polling).
      let sessions: SessionInfo[] = [];
      let activeSession: SessionInfo | null = null;
      try {
        const st = await daemonPost('windows-store/state', {});
        sessions = (st?.result?.sessions ?? st?.sessions) || [];
        activeSession = (st?.result?.active_session ?? st?.active_session) || null;
      } catch { /* best-effort */ }
      let official: OfficialWindow[] = [];
      let registered: WindowProfile[] = [];
      try {
        const wl = await daemonPost('windows-store/windows-list', {});
        official = (wl?.result?.official ?? wl?.official) || [];
        registered = (wl?.result?.registered ?? wl?.registered) || [];
      } catch { /* best-effort */ }

      setState({ ready: true, profiles: merged, liveLabels, remote, sessions, activeSession, official, registered, error: undefined });
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

/** Options d'ouverture d'une fenêtre par id (résolution auto). */
export interface OpenWindowExOpts {
  pos?: { x: number; y: number };
  size?: { width: number; height: number };
  title?: string;
  /** force le layout vierge "blank" (aucun arbre). */
  blank?: boolean;
}

/**
 * Ouvre une fenêtre par son id AVEC résolution auto (vivant → enregistré →
 * officiel) via windows-store/window-get scope=auto. Crée la fenêtre Tauri
 * (label = window_id → le boot retrouvera le layout) puis recrée le profil
 * VIVANT (windows-store/window-save scope=live) pour la session active.
 * Retourne le window_id. Best-effort : chaque étape est tolérante aux erreurs.
 */
export async function openWindowEx(windowId: string, opts?: OpenWindowExOpts): Promise<string> {
  // 1. Résolution auto : vivant → enregistré → officiel.
  let profile: any = null;
  try {
    const res = await daemonPost('windows-store/window-get', { window_id: windowId, scope: 'auto' });
    profile = res?.result?.profile ?? res?.profile ?? null;
  } catch { /* best-effort */ }

  // 1b. Fenêtre VIERGE : on force le layout "blank" (pas d'arbre) au lieu du
  //     layout vivant par défaut (qui pourrait être non vide).
  if (opts?.blank) {
    profile = { ...(profile ?? {}), template: 'blank', layout: 'blank', theme: profile?.theme ?? null };
  }

  // 2. Ouverture Tauri : label = window_id.
  const size = opts?.size ?? (profile?.width && profile?.height ? { width: profile.width, height: profile.height } : undefined);
  const pos = opts?.pos ?? (profile?.x != null && profile?.y != null ? { x: profile.x, y: profile.y } : undefined);
  const title = opts?.title ?? profile?.title ?? windowId;
  try {
    await createWindow(windowId, {
      ...(size ? { size } : {}),
      ...(pos ? { pos } : {}),
      ...(title ? { title } : {}),
    });
  } catch { /* web : fenêtre Tauri indisponible */ }

  // 3. Recrée le profil VIVANT (session active) pour restaurer layout/thème.
  try {
    await daemonPost('windows-store/window-save', {
      window_id: windowId,
      scope: 'live',
      profile: {
        template: profile?.template ?? 'default',
        layout: profile?.layout ?? layoutNameFor(windowId),
        theme: profile?.theme ?? null,
        title,
        x: pos?.x ?? null,
        y: pos?.y ?? null,
        width: size?.width ?? null,
        height: size?.height ?? null,
        state: 'normal',
      },
    });
  } catch { /* best-effort */ }

  // 4. Ajoute la fenêtre aux fenêtres OUVERTES de la session active (open_windows).
  try {
    const st = await daemonPost('windows-store/state', {});
    const active = st?.result?.active_session ?? st?.active_session;
    if (active?.id) {
      await daemonPost('win-session/add-window', { session_id: active.id, window_id: windowId });
    }
  } catch { /* best-effort */ }
  return windowId;
}

/**
 * Ouvre une fenêtre depuis un template (comportement historique).
 * Si `extra.label` est fourni (fenêtre PRÉENREGISTRÉE/enregistrée), on délègue
 * à openWindowEx (résolution auto du store) ; sinon on garde la logique
 * actuelle (label généré win-<ts> + profil windows/create).
 */
export async function openWindow(templateId: string, extra?: { pos?: { x: number; y: number }; label?: string; title?: string; size?: { width: number; height: number } }): Promise<string | null> {
  if (extra?.label) {
    return openWindowEx(extra.label, { pos: extra.pos, title: extra.title, size: extra.size });
  }
  const tpl = _state.templates.find((t) => t.id === templateId);
  const label = `win-${Date.now().toString(36)}`;
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

/** Nouvelle fenêtre vide : window_N → openWindowEx → retourne l'id. */
export async function newBlankWindow(): Promise<string> {
  let windowId = '';
  try {
    const res = await daemonPost('windows-store/next-window-id', {});
    windowId = res?.result?.window_id ?? res?.window_id ?? '';
  } catch { /* best-effort */ }
  if (!windowId) windowId = `win-${Date.now().toString(36)}`;
  await openWindowEx(windowId, { blank: true });
  return windowId;
}

/**
 * Met au premier plan une fenêtre si elle est déjà ouverte (liveLabels),
 * sinon l'ouvre (résolution auto). Signale la surbrillance dans le store.
 */
export async function focusOrOpenWindow(windowId: string): Promise<string | null> {
  if (_state.liveLabels.includes(windowId)) {
    try { await focusWindow(windowId); } catch { /* best-effort */ }
    setState({ highlightWindow: windowId });
    setTimeout(() => setState({ highlightWindow: null }), 2500);
    return windowId;
  }
  return openWindowEx(windowId);
}

/** Enregistre une fenêtre : vivant → register/ (RENOMME sous `name` si fourni). */
export async function registerWindow(windowId: string, name?: string, theme?: string, layout?: string): Promise<boolean> {
  try {
    const res = await daemonPost('windows-store/window-register', {
      window_id: windowId,
      ...(name && name !== windowId ? { new_window_id: name, title: name } : {}),
      ...(theme != null ? { theme } : {}),
      ...(layout != null ? { layout } : {}),
    });
    return (res?.result?.status ?? res?.status) === 'ok';
  } catch {
    return false;
  }
}

/** Réinitialise une fenêtre (supprime profil live/registered + layouts). */
export async function resetWindowLayout(windowId: string): Promise<boolean> {
  try {
    const res = await daemonPost('windows-store/window-reset', { window_id: windowId });
    return (res?.result?.status ?? res?.status) === 'ok';
  } catch {
    return false;
  }
}

/**
 * Persiste le verrou de thème (theme_locked) + le thème courant dans le profil
 * VIVANT de la fenêtre (session active). Best-effort.
 */
export async function persistWindowThemeLock(windowId: string, locked: boolean, theme?: string): Promise<boolean> {
  try {
    const cur = await daemonPost('windows-store/window-get', { window_id: windowId, scope: 'live' });
    const existing = cur?.result?.profile ?? cur?.profile ?? {};
    const profile = {
      ...existing,
      window_id: windowId,
      theme: theme ?? existing?.theme ?? null,
      theme_locked: locked,
    };
    const res = await daemonPost('windows-store/window-save', { window_id: windowId, scope: 'live', profile });
    return (res?.result?.status ?? res?.status) === 'ok';
  } catch {
    return false;
  }
}

/** Les 3 listes de fenêtres depuis le store (officielles/enregistrées/vivantes). */
export interface WindowSources {
  official: OfficialWindow[];
  registered: WindowProfile[];
  live: WindowProfile[];
}

export function listWindowSources(): WindowSources {
  return {
    official: _state.official,
    registered: _state.registered,
    live: _state.profiles.filter((p) => _state.liveLabels.includes(p.window_id)),
  };
}

// ── Sessions ──────────────────────────────────────────────────────────

/** Liste les sessions + l'id de la session active. */
export async function listSessions(): Promise<SessionInfo[]> {
  try {
    const res = await daemonPost('win-session/list', {});
    return (res?.result?.sessions ?? res?.sessions) || [];
  } catch {
    return [];
  }
}

/** Crée une session session_XXXX (ne change PAS la session active). */
export async function createSession(name?: string): Promise<SessionInfo | null> {
  try {
    const res = await daemonPost('win-session/create', { ...(name ? { name } : {}) });
    return (res?.result?.session ?? res?.session) || null;
  } catch {
    return null;
  }
}

/** Active une session (écrit session_open.txt côté daemon). */
export async function activateSession(id: string): Promise<SessionInfo | null> {
  try {
    const res = await daemonPost('win-session/activate', { session_id: id });
    return (res?.result?.session ?? res?.session) || null;
  } catch {
    return null;
  }
}

/** Ouvre (active) une session et retourne ses fenêtres ouvertes. */
export async function openSession(id: string): Promise<string[]> {
  try {
    const res = await daemonPost('win-session/open', { session_id: id });
    return (res?.result?.open_windows ?? res?.open_windows) || [];
  } catch {
    return [];
  }
}

/** Désactive une session si elle était active. */
export async function closeSession(id: string): Promise<void> {
  try { await daemonPost('win-session/close', { session_id: id }); } catch { /* best-effort */ }
}

/** Ferme TOUTES les fenêtres Tauri (hors celle courante) + vide open_windows. */
async function closeAllWindows(currentId?: string): Promise<void> {
  let live: string[] = [];
  try { live = (await tauriList()).map((w) => w.label); } catch { /* best-effort */ }
  for (const label of live) {
    if (currentId && label === currentId) continue;
    try { await closeWindow(label); } catch { /* best-effort */ }
  }
}

/** Ferme les fenêtres de la session ACTIVE (toutes sauf la fenêtre courante). */
export async function closeSessionWindows(currentId?: string): Promise<void> {
  await closeAllWindows(currentId);
}

/**
 * Bascule de session : ferme les fenêtres de la session courante (hors celle
 * courante), active la session cible, puis réouvre ses fenêtres « ouvertes ».
 * Si `id` vaut '__new__' : crée d'abord une nouvelle session par défaut.
 * La fenêtre COURANTE (si elle n'est pas 'main') est fermée APRÈS le switch
 * (délai), car elle appartenait à l'ancienne session.
 */
export async function switchSession(id: string, currentId?: string): Promise<string[]> {
  await closeAllWindows(currentId);
  let targetId = id;
  if (id === '__new__') {
    const created = await createSession();
    if (created?.id) targetId = created.id;
    else return [];
  }
  await activateSession(targetId);
  const openWindows = await openSession(targetId);
  for (const wid of openWindows) {
    if (wid === currentId) continue;
    try { await openWindowEx(wid); } catch { /* best-effort */ }
  }
  // Si la fenêtre d'origine n'est ni 'main' ni dans la nouvelle session, on la
  // ferme après un délai (elle ne fait plus partie de la session active).
  if (currentId && currentId !== 'main' && !openWindows.includes(currentId)) {
    setTimeout(() => { try { void closeWindow(currentId!); } catch { /* best-effort */ } }, 600);
  }
  return openWindows;
}

/** Renomme une session. */
export async function renameSession(id: string, name: string): Promise<SessionInfo | null> {
  try {
    const res = await daemonPost('win-session/rename', { session_id: id, name });
    return (res?.result?.session ?? res?.session) || null;
  } catch {
    return null;
  }
}

/** Définit le thème d'une session. */
export async function setSessionTheme(id: string, theme: string): Promise<SessionInfo | null> {
  try {
    const res = await daemonPost('win-session/set-theme', { session_id: id, theme });
    return (res?.result?.session ?? res?.session) || null;
  } catch {
    return null;
  }
}

/** Ajoute une fenêtre à open_windows d'une session. */
export async function addSessionWindow(id: string, wid: string): Promise<string[]> {
  try {
    const res = await daemonPost('win-session/add-window', { session_id: id, window_id: wid });
    return (res?.result?.open_windows ?? res?.open_windows) || [];
  } catch {
    return [];
  }
}

/** Retire une fenêtre de open_windows d'une session. */
export async function removeSessionWindow(id: string, wid: string): Promise<string[]> {
  try {
    const res = await daemonPost('win-session/remove-window', { session_id: id, window_id: wid });
    return (res?.result?.open_windows ?? res?.open_windows) || [];
  } catch {
    return [];
  }
}

/** Supprime une session (et la déactive si c'était l'active). */
export async function deleteSession(id: string): Promise<boolean> {
  try {
    const res = await daemonPost('win-session/delete', { session_id: id });
    return (res?.result?.status ?? res?.status) === 'ok';
  } catch {
    return false;
  }
}

/** Fenêtres ouvertes (ids) d'une session. */
export async function sessionOpenWindows(id: string): Promise<string[]> {
  try {
    const res = await daemonPost('win-session/open-windows', { session_id: id });
    return (res?.result?.open_windows ?? res?.open_windows) || [];
  } catch {
    return [];
  }
}

// ── Actions fenêtres (Tauri + daemon) ─────────────────────────────────

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

/** Plein écran SESSION : applique (ou sort) le plein écran à TOUTES les fenêtres. */
export async function toggleFullscreenSession(fullscreen: boolean): Promise<boolean> {
  try {
    const res = await invoke('fullscreen_session', { fullscreen });
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
