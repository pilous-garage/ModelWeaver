// loader.ts — chargement paresseux des panels EXTERNES (architecture hybride).
//
// Les panels `essential` sont compilés dans le bundle GUI (registry local).
// Les panels EXTERNES sont compilés par panel-creator (esbuild), servis par le
// daemon (GET /v1/panels/file/<id>, SANS auth — import() ne peut pas envoyer
// le Bearer), et chargés ici via import() dynamique à la demande.
//
// React est PARTAGÉ : les panels externes importent React via l'URL du daemon
// (/_shared/react) qui ré-exporte window.React.

import type { PanelDef } from './contract.ts';
import { registerPanel, getPanel, listPanels } from './registry.ts';
import { daemonPost, daemonBaseUrl } from '../bridge.ts';

export interface ExternalPanelStatus {
  id: string;
  label?: string;
  version?: string;
  loaded: boolean;
  loading?: boolean;
  error?: string;
}

const _status: Record<string, ExternalPanelStatus> = {};
const _known: Record<string, { id: string; file?: string }> = {};

/** Panels externes connus (index) — pour le catalogue, sans chargement. */
export function listExternalCandidates(): { id: string; labelKey?: string }[] {
  return Object.values(_known).map((k) => ({
    id: k.id,
    labelKey: _status[k.id]?.label,
  }));
}

export function getPanelLoadStatus(id: string): ExternalPanelStatus | undefined {
  return _status[id];
}

export function getAllPanelStatus(): Record<string, ExternalPanelStatus> {
  return { ..._status };
}

/** Liste les panels externes connus (index du daemon, sans les charger). */
export async function loadExternalPanelIndex(): Promise<ExternalPanelStatus[]> {
  const results: ExternalPanelStatus[] = [];
  try {
    const res = await daemonPost('panels/index', {});
    const panels = res?.result?.panels ?? res?.panels ?? [];
    for (const p of panels) {
      _known[p.id] = { id: p.id, file: p.file };
      _status[p.id] = { id: p.id, label: p.label, version: p.version, loaded: false };
      results.push(_status[p.id]);
    }
  } catch {
    // best-effort
  }
  return results;
}

/**
 * S'assure qu'un panel est chargé (essentiel OU externe paresseux).
 * Retourne le PanelDef ou null si inconnu/échec.
 */
export async function ensurePanelLoaded(id: string): Promise<PanelDef | null> {
  const local = getPanel(id);
  if (local) return local;
  if (!_known[id]) {
    _status[id] = { id, loaded: false, loading: true };
    _known[id] = { id };
  }
  if (_status[id]?.loading || _status[id]?.loaded) {
    return getPanel(id) || null;
  }
  return loadExternalPanel(id);
}

/** Charge un panel externe précis et l'enregistre dans le registry. */
async function loadExternalPanel(id: string): Promise<PanelDef | null> {
  _status[id] = { id, loading: true, loaded: false };
  try {
    const base = await daemonBaseUrl();
    const url = `${base}/v1/panels/file/${id}`;
    const mod = await import(/* @vite-ignore */ url);
    const panel: PanelDef | undefined = mod?.Panel ?? mod?.default;
    if (!panel || !panel.id || !panel.component) {
      throw new Error(`contrat Panel invalide pour '${id}'`);
    }
    panel.essential = false; // externe

    // Adaptation contrat V1 → V2 : les panels V1 ont `label` (pas labelKey) et
    // un composant qui reçoit un ctx "app complet". On normalise le déf.
    const v1 = panel as any;
    if (!panel.labelKey && v1.label) panel.labelKey = v1.label;
    if (!panel.iconKey && v1.icon) panel.iconKey = v1.icon;
    if (v1.description && !panel.declaration) {
      const desc = v1.description;
      panel.declaration = () => `[${panel.id}] ${desc}`;
    }

    registerPanel(panel);
    _status[id] = { id, label: panel.labelKey, version: panel.version, loaded: true };
    return panel;
  } catch (e: any) {
    _status[id] = { id, loaded: false, error: String(e?.message || e) };
    return null;
  }
}

/** Charge tous les panels d'un bundle (à la demande). */
export async function loadBundle(bundleName: string): Promise<PanelDef[]> {
  const loaded: PanelDef[] = [];
  try {
    const res = await daemonPost('panels/bundles/get', { name: bundleName });
    const bundle = res?.result?.bundle ?? res?.bundle;
    const panels: string[] = bundle?.panels || [];
    for (const pid of panels) {
      const p = await ensurePanelLoaded(pid);
      if (p) loaded.push(p);
    }
  } catch {
    // best-effort
  }
  return loaded;
}

/** Fusionne les panels externes connus + locaux dans le catalogue. */
export async function refreshExternalCatalogue(): Promise<PanelDef[]> {
  await loadExternalPanelIndex();
  return listPanels();
}
