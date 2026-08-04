// Loader Runtime des panels EXTERNES (architecture hybride).
//
// Le monolithe contient les panels `essential: true` (compilés dans le bundle).
// Les panels EXTERNES sont compilés isolément par `panel-creator` (esbuild),
// servis par le daemon (panels/file/<id>) et chargés ici via import() dynamique.
//
// React est PARTAGÉ (exposé en global par la GUI) : les panels externes
// ne re-bundlent pas React. L'option AUTONOME existe (panel bundlé avec son
// React) mais n'est pas requise pour le chargement standard.

import type { PanelDef } from '../types.ts';
import { daemonPost } from '../bridge.ts';
import { PANEL_REGISTRY } from './index.ts';

// Pour l'URL du fichier JS servi par le daemon (import() dynamique).
const _baseUrl = () => `http://127.0.0.1:8770`;

export interface ExternalPanelStatus {
  id: string;
  label?: string;
  version?: string;
  loaded: boolean;
  error?: string;
}

const _status: Record<string, ExternalPanelStatus> = {};

export function getPanelLoadStatus(id: string): ExternalPanelStatus | undefined {
  return _status[id];
}

export function getAllPanelStatus(): Record<string, ExternalPanelStatus> {
  return { ..._status };
}

/**
 * Charge les panels externes depuis le registre du daemon.
 * Chaque panel compilé est importé dynamiquement et ajouté au PANEL_REGISTRY.
 */
export async function loadExternalPanels(): Promise<ExternalPanelStatus[]> {
  const results: ExternalPanelStatus[] = [];
  try {
    const res = await daemonPost('panels/index', {});
    const panels = (res?.result?.panels) || (res?.panels) || [];
    for (const p of panels) {
      results.push(await loadExternalPanel(p));
    }
  } catch (e) {
    console.warn('[panels.loader] échec index:', e);
  }
  return results;
}

/** Charge un panel externe précis (id ou {id, file}) et l'ajoute au registry. */
export async function loadExternalPanel(spec: { id: string; file?: string }): Promise<ExternalPanelStatus> {
  const id = spec.id;
  try {
    // L'URL du fichier : le panneau est servi par le daemon.
    // En dev Vite, les modules servis en HTTP doivent être valides ESM.
    const jsUrl = `${_baseUrl()}/v1/panels/file/${id}`;
    const mod = await import(/* @vite-ignore */ jsUrl);
    const panel: PanelDef | undefined = mod?.Panel ?? mod?.default;
    if (!panel || !panel.id || !panel.component) {
      throw new Error(`contrat Panel invalide pour '${id}'`);
    }
    // Marquer comme externe (pas essentiel).
    panel.essential = false;
    PANEL_REGISTRY[panel.id] = panel;
    _status[id] = { id, label: panel.label, version: panel.version, loaded: true };
  } catch (e: any) {
    _status[id] = { id, loaded: false, error: String(e?.message || e) };
    console.warn(`[panels.loader] échec chargement '${id}':`, e);
  }
  return _status[id];
}

/**
 * Expose React (et ReactDOM) en global pour les panels qui ne peuvent pas
 * import() node_modules (chargés en ESM depuis le daemon).
 */
export function exposeSharedGlobals() {
  const w = window as any;
  if (!w.React) {
    try {
      // Import direct : Vite résout react depuis node_modules.
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const React = require('react');
      w.React = React;
    } catch { /* React exposé via le bundle sinon */ }
  }
}
