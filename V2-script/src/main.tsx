// main.tsx — point d'entrée : init panels, charge le layout, rend l'app.

import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App.tsx';
import { initPanels } from './panels/init.ts';
import { loadExternalPanelIndex } from './panels/loader.ts';
import { layoutFromYaml } from './layout/persist.ts';
import { applyThemeFromLayout } from './theme.ts';
import { daemonPost } from './bridge.ts';
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
      active: occ-3
`;

/** Résout le layout de la fenêtre :
 * 1. Profil de fenêtre (windows/list) → son layout référencé (ex. "monitoring").
 * 2. Sinon layout-<windowId> (sauvegarde temps réel de cette fenêtre).
 * 3. Sinon layout par défaut.
 */
async function loadWindowLayout(windowId: string): Promise<{ layout: Layout; profile?: any }> {
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
  // Label réel de la fenêtre (injecté par le binaire Tauri dans chaque webview)
  const windowId = (window as any).__MW_WINDOW_LABEL || 'main';
  const { layout, profile } = await loadWindowLayout(windowId);
  // Thème : le profil de la fenêtre prime (thème préenregistré), sinon le layout.
  await applyThemeFromLayout(profile?.theme ? { global: profile.theme } : layout.theme);

  const root = document.getElementById('root');
  if (!root) return;
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <App layout={layout} windowId={windowId} />
    </React.StrictMode>,
  );
}

bootstrap();
