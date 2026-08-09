// main.tsx — point d'entrée : init panels, charge le layout, rend l'app.

import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App.tsx';
import { initPanels } from './panels/init.ts';
import { loadExternalPanelIndex } from './panels/loader.ts';
import { layoutFromYaml } from './layout/persist.ts';
import { applyThemeFromLayout } from './theme.ts';
import { daemonPost, listWindows } from './bridge.ts';
import { openWindowEx } from './windows.ts';
import type { Layout } from './layout/types.ts';

// React PARTAGÉ : les panels externes compilés importent React via l'URL du
// daemon (/_shared/react) qui ré-exporte window.React / window.ReactDOM.
const g = window as any;
if (!g.React) g.React = React;
if (!g.ReactDOM) g.ReactDOM = ReactDOM;

// Layout par défaut (démo) — en production : chargé depuis la session.
const DEFAULT_LAYOUT_YAML = `
id: layout-__WIN__
label: "Principale"
theme:
  global: dark
tree:
  type: split
  direction: horizontal
  children:
    - type: group
      id: pg-gauche
      tabs:
        - { panel: ressources, occId: occ-1, params: { vue: detail } }
        - { panel: ressources-variant, occId: occ-2 }
      active: occ-1
    - type: group
      id: pg-droit
      tabs:
        - { panel: etat-systeme, occId: occ-3 }
        - { panel: etat-simple, occId: occ-4 }
        - { panel: autorisations, occId: occ-autorisations }
      active: occ-3
`;

/** Résout le layout de la fenêtre :
 * 0. Résolution AUTO via le store (vivant → enregistré → officiel).
 * 1. Profil de fenêtre (windows/list) → son layout référencé (ex. "monitoring").
 * 2. Sinon layout-<windowId> (sauvegarde temps réel de cette fenêtre).
 * 3. Sinon layout par défaut.
 */
async function loadWindowLayout(windowId: string): Promise<{ layout: Layout; profile?: any }> {
  // 0. Store structuré (sessions) : résolution vivant → enregistré → officiel.
  try {
    const ws = await daemonPost('windows-store/window-get', { window_id: windowId, scope: 'auto' });
    const profile = ws?.result?.profile ?? ws?.profile;
    if (profile?.layout) {
      const res = await daemonPost('layout/get', { name: profile.layout });
      if (res?.result?.yaml ?? res?.yaml) {
        return { layout: layoutFromYaml(res?.result?.yaml ?? res?.yaml), profile };
      }
    }
  } catch {
    // best-effort
  }
  // 1. profil de la fenêtre (fenêtres préenregistrées / windows/create)
  try {
    const wp = await daemonPost('windows/list', {});
    const profiles = wp?.result?.windows ?? wp?.windows ?? [];
    const profile = profiles.find((p: any) => p.window_id === windowId);
    if (profile?.layout) {
      const res = await daemonPost('layout/get', { name: profile.layout });
      if (res?.result?.yaml ?? res?.yaml) {
        return { layout: layoutFromYaml(res?.result?.yaml ?? res?.yaml), profile };
      }
    }
  } catch {
    // best-effort
  }
  // 2. layout temps réel de cette fenêtre
  try {
    const res = await daemonPost('layout/get', { name: `layout-${windowId}` });
    if (res?.result?.yaml ?? res?.yaml) return { layout: layoutFromYaml(res?.result?.yaml ?? res?.yaml) };
  } catch {
    // pas de layout sauvegardé → défaut
  }
  return { layout: layoutFromYaml(DEFAULT_LAYOUT_YAML.replace('__WIN__', windowId)) };
}

async function bootstrap() {
  await initPanels();
  // Langue persistée (option) : chargée avant le rendu pour un menu cohérent.
  const { loadSavedLocale } = await import('./i18n.ts');
  await loadSavedLocale(daemonPost);
  // Index des panels externes (catalogue complet, chargés à la demande).
  try {
    await loadExternalPanelIndex();
  } catch {
    // best-effort : catalogue = panels essentiels uniquement
  }
  // Label réel de la fenêtre (injecté par le binaire Tauri dans chaque webview).
  // NB : après un page reload (HMR/dev), __MW_WINDOW_LABEL est perdu (le script
  // d'injection ne se rejoue pas) → TOUTES les fenêtres retomberaient sur 'main'
  // et chargeraient le mauvais layout. On récupère le label via IPC Tauri
  // (window_label), source fiable, avec fallback sur la variable injectée.
  let windowId = (window as any).__MW_WINDOW_LABEL || 'main';
  try {
    if ((window as any).__TAURI_INTERNALS__) {
      const core = await import('@tauri-apps/api/core');
      const lbl: string = await core.invoke('window_label');
      if (lbl) windowId = lbl;
    }
  } catch {
    // best-effort : on garde __MW_WINDOW_LABEL / 'main'
  }
  const { layout, profile } = await loadWindowLayout(windowId);
  // Thème : theme-lock (fenêtre) → session active → profil/layout.
  const theme = await resolveTheme(windowId, profile?.theme);
  await applyThemeFromLayout(theme ? { global: theme } : layout.theme);

  const root = document.getElementById('root');
  if (!root) return;
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <App layout={layout} windowId={windowId} />
    </React.StrictMode>,
  );

  // Restauration de la session active APRÈS le rendu initial (délai ~1200ms),
  // uniquement depuis la fenêtre 'main'. Guard anti-boucle par sessionStorage.
  setTimeout(() => {
    void restoreActiveSession(windowId);
  }, 1200);
}

/**
 * Restaure la session active au boot : réouvre ses fenêtres (sauf la fenêtre
 * courante et celles déjà ouvertes), ou crée/active la session par défaut si
 * aucune n'est active. Best-effort : toute erreur est ignorée.
 */
async function restoreActiveSession(windowId: string): Promise<void> {
  if (windowId !== 'main') return;
  try {
    if (sessionStorage.getItem('__MW_SESSION_BOOTED__')) return;
    sessionStorage.setItem('__MW_SESSION_BOOTED__', '1');
  } catch {
    // sessionStorage indisponible → guard perdu, on continue en best-effort
  }

  try {
    // a) Session active du store
    const ws = await daemonPost('windows-store/state', {});
    const state = ws?.result ?? ws;
    const active = state?.active_session ?? null;

    if (active?.id) {
      // Session active existante (même vide) : on la garde, on rouvre ses
      // fenêtres ouvertes (sauf déjà vivantes).
      if (Array.isArray(active.open_windows) && active.open_windows.length) {
        let live: string[] = [];
        try {
          live = (await listWindows()).map((w) => w.label);
        } catch { /* best-effort */ }
        for (const wid of active.open_windows) {
          if (wid === windowId) continue;
          if (live.includes(wid)) continue;
          try { await openWindowEx(wid); } catch { /* best-effort */ }
        }
      }
      return;
    }

    // c) Aucune session active → session par défaut + activation.
    try {
      const created = await daemonPost('win-session/create', { name: 'Session par défaut' });
      const sid = created?.result?.session?.id ?? created?.session?.id;
      if (sid) await daemonPost('win-session/activate', { session_id: sid });
    } catch { /* best-effort */ }
  } catch {
    // best-effort
  }
}

/**
 * Résout le thème d'une fenêtre au boot :
 *   theme-lock (fenêtre) → session active → profil de fenêtre → null (layout).
 * Expose aussi window.__MW_THEME_LOCKED__ (utilisé par le broadcast du thème de
 * session : une fenêtre lockée ignore les changements de thème de session).
 */
async function resolveTheme(windowId: string, profileTheme?: string | null): Promise<string | null> {
  // 1. theme-lock : le profil vivant de la fenêtre porte un thème forcé.
  try {
    const ws = await daemonPost('windows-store/window-get', { window_id: windowId, scope: 'live' });
    const live = ws?.result?.profile ?? ws?.profile;
    (window as any).__MW_THEME_LOCKED__ = Boolean(live?.theme_locked);
    if (live?.theme_locked && live?.theme) return live.theme;
  } catch { /* best-effort */ }
  // 2. session active (thème de session).
  try {
    const st = await daemonPost('windows-store/state', {});
    const active = st?.result?.active_session ?? st?.active_session;
    if (active?.theme) return active.theme;
  } catch { /* best-effort */ }
  // 3. profil de la fenêtre.
  return profileTheme ?? null;
}

bootstrap();
