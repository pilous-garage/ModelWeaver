// Loader Runtime des panels EXTERNES (architecture hybride).
//
// Le monolithe contient les panels `essential: true` (compilés dans le bundle).
// Les panels EXTERNES sont compilés isolément par `panel-creator` (esbuild),
// servis par le daemon (panels/file/<id>) et chargés ici via import() dynamique.
//
// CHARGEMENT PARESSEUX : on ne charge un panel externe QUE quand une fenêtre /
// un layout le référence (ensurePanelLoaded). On ne les charge pas tous au
// boot — lisibilité + démarrage rapide.
//
// React est PARTAGÉ (exposé en global par la GUI) : les panels externes
// importent React via l'URL du daemon qui ré-exporte window.React.

import type { PanelDef } from '../types.ts';
import { daemonPost } from '../bridge.ts';
import { PANEL_REGISTRY } from './index.ts';
import { logGui } from '../gui_log.ts';

const _baseUrl = () => `http://127.0.0.1:8770`;

export interface ExternalPanelStatus {
  id: string;
  label?: string;
  version?: string;
  loaded: boolean;
  loading?: boolean;
  error?: string;
}

const _status: Record<string, ExternalPanelStatus> = {};
// Panels externes connus (index du daemon) sans les avoir chargés.
let _known: Record<string, { id: string; file?: string }> = {};

export function getPanelLoadStatus(id: string): ExternalPanelStatus | undefined {
  return _status[id];
}

export function getAllPanelStatus(): Record<string, ExternalPanelStatus> {
  return { ..._status };
}

/** Récupère la liste des panels externes connus (sans les charger). */
export async function loadExternalPanelIndex(): Promise<ExternalPanelStatus[]> {
  const results: ExternalPanelStatus[] = [];
  try {
    const res = await daemonPost('panels/index', {});
    const panels = (res?.result?.panels) || (res?.panels) || [];
    _known = {};
    for (const p of panels) {
      _known[p.id] = { id: p.id, file: p.file };
      _status[p.id] = { id: p.id, label: p.label, version: p.version, loaded: false };
      results.push(_status[p.id]);
    }
  } catch (e) {
    console.warn('[panels.loader] échec index:', e);
  }
  return results;
}

/**
 * S'assure qu'un panel externe est chargé. Si absent du registry ET connu
 * comme externe → import() dynamique (chargement paresseux à la demande).
 * Retourne le PanelDef ou null.
 */
export async function ensurePanelLoaded(id: string): Promise<PanelDef | null> {
  if (PANEL_REGISTRY[id]) return PANEL_REGISTRY[id];  // déjà chargé (essentiel ou externe)
  if (!_known[id]) {
    // Pas dans l'index connu — on le cherche et on le marque external.
    _status[id] = { id, loaded: false, loading: true };
    _known[id] = { id };
  }
  if (_status[id]?.loading || _status[id]?.loaded) {
    // charge en cours / déjà tenté → attendre la fin (Promise en cache).
    if (_status[id]?.loaded) return PANEL_REGISTRY[id] || null;
  }
  return loadExternalPanel({ id });
}

/** Charge un panel externe précis et l'ajoute au registry. */
async function loadExternalPanel(spec: { id: string; file?: string }): Promise<PanelDef | null> {
  const id = spec.id;
  _status[id] = { id, loading: true, loaded: false };
  try {
    const jsUrl = `${_baseUrl()}/v1/panels/file/${id}`;
    const mod = await import(/* @vite-ignore */ jsUrl);
    const panel: PanelDef | undefined = mod?.Panel ?? mod?.default;
    if (!panel || !panel.id || !panel.component) {
      throw new Error(`contrat Panel invalide pour '${id}'`);
    }
    panel.essential = false;  // externe
    PANEL_REGISTRY[panel.id] = panel;
    _status[id] = { id, label: panel.label, version: panel.version, loaded: true };
    logGui('panel:loaded', { id, label: panel.label, version: panel.version });
    return panel;
  } catch (e: any) {
    _status[id] = { id, loaded: false, error: String(e?.message || e) };
    logGui('panel:load-error', { id, error: String(e?.message || e) });
    console.warn(`[panels.loader] échec chargement '${id}':`, e);
    return null;
  }
}

/** Charge tous les panels d'un bundle (à la demande). */
export async function loadBundle(bundleName: string): Promise<PanelDef[]> {
  const loaded: PanelDef[] = [];
  try {
    const res = await daemonPost('panels/bundles/get', { name: bundleName });
    const bundle = res?.result?.bundle || res?.bundle;
    const panels: string[] = bundle?.panels || [];
    for (const pid of panels) {
      const p = await ensurePanelLoaded(pid);
      if (p) loaded.push(p);
    }
  } catch (e) {
    console.warn(`[panels.loader] échec bundle '${bundleName}':`, e);
  }
  return loaded;
}
