// Registre des panels — source de vérité des panels disponibles.
// Chaque panel expose un PanelDef (contrat complet, SPEC §13.2).

import type { PanelDef } from './contract.ts';
import { loadLangYaml, loadDefaultLang } from '../i18n.ts';

const registry: Record<string, PanelDef> = {};

/** Enregistre un panel + charge son lang. */
export function registerPanel(def: PanelDef): void {
  registry[def.id] = def;
}

export function getPanel(id: string): PanelDef | undefined {
  return registry[id];
}

export function listPanels(): PanelDef[] {
  return Object.values(registry);
}

export function listEssentialPanels(): PanelDef[] {
  return listPanels().filter((p) => p.essential);
}

/** Charge les lang de tous les panels + le lang par défaut (fr + en). */
export async function loadAllPanelLangs(): Promise<void> {
  loadDefaultLang();
  for (const p of listPanels()) {
    // langEmbedded = FR ; langEmbeddedEn = EN (multilingue)
    const fr = (p as any).langEmbedded;
    if (fr && typeof fr === 'string') loadLangYaml(fr, 'fr');
    const en = (p as any).langEmbeddedEn;
    if (en && typeof en === 'string') loadLangYaml(en, 'en');
  }
}

export { registry as PANEL_REGISTRY };
