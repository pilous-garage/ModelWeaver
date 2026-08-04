// ⚡ Généré par scripts/discover-panels.ts
// Panneaux découverts automatiquement depuis src/panels/

import type { PanelDef } from '../types.ts';

// Architecture hybride :
//  - panels ESSENTIELS (essential: true) → chargés ici (monolithe, bundle GUI)
//  - panels EXTERNES (essential faux/absent) → compilés par panel-creator,
//    chargés paresseusement via panels/loader.ts (import() à la demande)
const registry: Record<string, PanelDef> = {};

// Import dynamique des modules découverts (eager = inclus dans le bundle)
const panelContext = import.meta.glob('./**/*.panel.tsx', { eager: true });

for (const [path, mod] of Object.entries(panelContext)) {
  const panel = (mod as any).Panel;
  if (!panel || !panel.id) {
    console.warn(`[panels] ${path}: pas d'export Panel valide`);
    continue;
  }
  if (registry[panel.id]) {
    console.error(`[panels] ERREUR: id="${panel.id}" en conflit (${path})`);
    continue;
  }
  // Ne garder au monolithe que les panels essentiels ; les non-essentiels
  // restent DISPONIBLES ici (au cas où) mais sont surtout chargés à la demande.
  if (panel.essential !== false) {
    registry[panel.id] = panel;
  }
}

export const PANEL_REGISTRY: Record<string, PanelDef> = registry;

export function getPanelDeclaration(id: string): string | null {
  const p = PANEL_REGISTRY[id];
  return p?.declaration?.() ?? null;
}

export function listAllPanelDeclarations(): string[] {
  return Object.entries(PANEL_REGISTRY).map(([id, p]) => `--- ${id} ---\n${p.declaration?.() ?? '(aucune)'}`);
}
